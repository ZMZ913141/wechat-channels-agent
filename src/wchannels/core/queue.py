"""发布队列：错峰排期、定时任务、自动执行顺序。"""
from sqlmodel import Session, select
from datetime import datetime, timedelta
from ..models import (
    PublishBatch, PublishTarget, TargetStatus, BatchStatus,
    Account, Material,
)
from ..config import settings
from ..timeutils import now as utcnow
from . import material_account_configs as mac_svc


class OwnershipError(ValueError):
    """请求中含有不属于当前用户的素材/账号。"""


def _owned_ids(session: Session, model, ids: list[int], user_id: int, label: str) -> list[int]:
    """只保留属于该用户的 ID；一个都不合法则报错，避免静默建出空批次。"""
    if not ids:
        raise OwnershipError(f"未选择任何{label}")
    rows = list(session.exec(select(model).where(model.id.in_(ids), model.user_id == user_id)).all())
    valid = [r.id for r in rows]
    if not valid:
        raise OwnershipError(f"所选{label}不属于当前用户")
    if len(valid) != len(set(ids)):
        # 部分越权：保留合法部分，忽略非法 ID（不中断正常排期）
        valid = sorted(set(valid))
    return valid


def create_batch(
    session: Session, user_id: int,
    material_ids: list[int], account_ids: list[int],
    stagger_min: int | None = None,
) -> PublishBatch:
    """选 N 个素材 × M 个账号，自动错峰排期。

    归属校验：只接受属于 user_id 的素材与账号——否则用户可把别人的
    素材排到调度器队列里，被自动发布到别人的号上。
    """
    material_ids = _owned_ids(session, Material, material_ids, user_id, "素材")
    account_ids = _owned_ids(session, Account, account_ids, user_id, "账号")

    batch = PublishBatch(user_id=user_id, status=BatchStatus.QUEUED.value)
    session.add(batch)
    session.commit()
    session.refresh(batch)

    now_dt = utcnow()
    # 错峰间隔按账号所属平台取（此前只读全局 PUBLISH_STAGGER_MIN，
    # 配置里 PLATFORM_STAGGER_MIN 每平台错峰间隔实际从未生效）。
    # 用时间游标累加而不是 slot×间隔：混合平台时后者会算出非单调的排期时间。
    accounts = {a.id: a for a in session.exec(
        select(Account).where(Account.id.in_(account_ids))).all()}
    cursor = now_dt
    first = True
    for mid in material_ids:
        m = session.get(Material, mid)
        for aid in account_ids:
            a = accounts.get(aid)
            stagger = stagger_min or settings.PLATFORM_STAGGER_MIN.get(
                a.platform if a else "wechat", settings.PUBLISH_STAGGER_MIN)
            if not first:
                # 第一条保持 now：一键发布后调度器 30 秒内就开始执行，
                # 用户立刻能看到动静；后续按各平台间隔错峰
                cursor = cursor + timedelta(minutes=stagger)
            first = False
            target = PublishTarget(
                batch_id=batch.id,
                material_id=mid,
                account_id=aid,
                scheduled_at=cursor,
                status=TargetStatus.QUEUED.value,
            )
            # 优先取「该素材×该账号」的独立配置，没有再回退到素材默认
            cfg = mac_svc.get_config(session, mid, aid)
            if cfg:
                target.short_title = cfg.short_title
                target.description = cfg.description
                target.topics = cfg.topics
                target.ai_disclosure = cfg.ai_disclosure
                target.collection = cfg.collection
                target.cover_path = cfg.cover_path
                target.comment = cfg.comment
                target.original_work = cfg.original_work
                target.visibility = cfg.visibility or "public"
            if m and not target.short_title:
                target.short_title = m.short_title
            if m and not target.description:
                target.description = m.description
            if m and not target.topics:
                target.topics = m.topics
            if m and not target.ai_disclosure:
                target.ai_disclosure = m.ai_disclosure
            if m and not target.collection:
                target.collection = m.collection
            if m and not target.cover_path:
                target.cover_path = m.cover_path
            # 账号默认话题：素材没配话题时用账号的
            if a and a.default_topics and not target.topics:
                target.topics = a.default_topics
            session.add(target)
    session.commit()
    return batch


def due_targets(session: Session, now_dt: datetime | None = None) -> list[PublishTarget]:
    """取出到点该跑的目标（状态 queued 且 scheduled_at <= now）。"""
    now_dt = now_dt or utcnow()
    q = select(PublishTarget).where(
        PublishTarget.status == TargetStatus.QUEUED.value,
        PublishTarget.scheduled_at <= now_dt,
    ).order_by(PublishTarget.scheduled_at)
    return list(session.exec(q).all())


def mark_running(session: Session, target: PublishTarget) -> None:
    target.status = TargetStatus.RUNNING.value
    session.add(target)
    session.commit()


def mark_success(session: Session, target: PublishTarget, account: Account, material: Material) -> None:
    target.status = TargetStatus.SUCCESS.value
    target.published_at = utcnow()
    account.last_publish_at = utcnow()
    account.daily_publish_count += 1
    from .materials import mark_published
    mark_published(session, material)
    session.add(target)
    session.add(account)
    session.commit()


def mark_failed(session: Session, target: PublishTarget, message: str, screenshot: str = "") -> None:
    target.status = TargetStatus.FAILED.value
    target.result_message = message
    target.screenshot_path = screenshot
    session.add(target)
    session.commit()


def batch_status(session: Session, batch: PublishBatch) -> dict:
    targets = list(session.exec(select(PublishTarget).where(PublishTarget.batch_id == batch.id)).all())
    total = len(targets)
    done = sum(1 for t in targets if t.status in (TargetStatus.SUCCESS.value, TargetStatus.FAILED.value))
    success = sum(1 for t in targets if t.status == TargetStatus.SUCCESS.value)
    failed = sum(1 for t in targets if t.status == TargetStatus.FAILED.value)
    return {"total": total, "done": done, "success": success, "failed": failed}
