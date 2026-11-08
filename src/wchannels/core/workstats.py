"""作品数据采集：打开各账号的创作者后台，读作品列表并落库成按天快照。"""
from datetime import datetime

from sqlmodel import Session, select

from ..browser.launcher import launch_context
from ..browser.platforms import collect_for_account
from ..browser.proclock import AccountLock, ProfileLock, LockBusy
from ..config import settings
from ..models import (Account, AccountStatus, Material, MaterialStatus,
                      PublishTarget, TargetStatus, WorkStat)
from ..timeutils import now
from ..logutil import get_logger

log = get_logger("workstats")


def _norm(txt: str) -> str:
    """去掉空白与常见标点，只留文字，用于「平台作品」与「本地任务」模糊匹配。"""
    keep = []
    for ch in (txt or ""):
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            keep.append(ch)
    return "".join(keep)


def _match_target(session: Session, account: Account, work: dict) -> PublishTarget | None:
    """把平台作品对回本地发布任务：按标题/描述内容比对（平台不返回本地 ID）。"""
    key = _norm(work.get("work_title") or work.get("description") or "")
    if not key:
        return None
    targets = session.exec(
        select(PublishTarget)
        .where(PublishTarget.account_id == account.id,
               PublishTarget.status == TargetStatus.SUCCESS.value)
        .order_by(PublishTarget.id.desc())
        .limit(60)
    ).all()
    best, best_score = None, 0
    for t in targets:
        cand = _norm(t.description or t.short_title)
        if not cand:
            continue
        if cand[:12] and cand[:12] in key or key[:12] in cand:
            score = min(len(cand), len(key))
            if score > best_score:
                best, best_score = t, score
    return best


def save_works(session: Session, account: Account, works: list[dict]) -> int:
    """把一次采集结果写入 WorkStat（同一作品同一天只保留一条，重复采集更新数值）。"""
    day = now().strftime("%Y-%m-%d")
    saved = 0
    for w in works:
        wid = (w.get("work_id") or "").strip()
        if not wid:
            continue
        row = session.exec(
            select(WorkStat).where(WorkStat.account_id == account.id,
                                   WorkStat.work_id == wid,
                                   WorkStat.stat_date == day)
        ).first()
        if not row:
            row = WorkStat(user_id=account.user_id, account_id=account.id,
                           platform=account.platform, work_id=wid, stat_date=day)
        target = _match_target(session, account, w)
        if target:
            row.target_id = target.id
            row.material_id = target.material_id
        row.work_url = w.get("work_url") or row.work_url
        row.work_title = (w.get("work_title") or "")[:80]
        row.views = int(w.get("views") or 0)
        row.likes = int(w.get("likes") or 0)
        row.comments = int(w.get("comments") or 0)
        row.shares = int(w.get("shares") or 0)
        row.favorites = int(w.get("favorites") or 0)
        row.collected_at = now()
        pub = w.get("published_at")
        if pub and not row.published_at:
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    # 平台展示的是本地时间；astimezone() 把 naive 当本地时区并转成带时区时间
                    row.published_at = datetime.strptime(pub, fmt).astimezone()
                    break
                except Exception:
                    continue
        session.add(row)
        saved += 1
    session.commit()

    # 有数据的作品视为「已发布」，避免素材一直躺在待发布里被重复发
    mids = {r.material_id for r in session.exec(
        select(WorkStat).where(WorkStat.account_id == account.id, WorkStat.stat_date == day)
    ).all() if r.material_id}
    for mid in mids:
        m = session.get(Material, mid)
        if m and m.status != MaterialStatus.PUBLISHED.value:
            m.status = MaterialStatus.PUBLISHED.value
    session.commit()
    return saved


def collect_account(account: Account) -> tuple[list[dict], str]:
    """采集单个账号的作列表。失败返回 (空, 原因)，不抛异常。

    统一用「有头但移出屏幕」的窗口：视频号会针对纯无头模式让登录态失效，
    用无头采集会在第二天被迫重新扫码。
    """
    profile = settings.profiles_dir / account.profile_dir
    a_lock, p_lock = AccountLock(account.id), ProfileLock(profile)
    try:
        a_lock.acquire()
        p_lock.acquire()
    except LockBusy:
        return [], "账号正忙（有发布任务在执行），稍后再采"
    try:
        with launch_context(profile, headless=False,
                            extra_args=["--window-position=-32000,-32000"]) as ctx:
            return collect_for_account(ctx, account)
    except Exception as e:
        log.warning("采集 %s 失败：%s", account.name, e)
        return [], f"采集失败：{e}"
    finally:
        a_lock.release()
        p_lock.release()


def collect_all(session: Session, user_id: int | None = None) -> list[dict]:
    """采集所有 active 账号。返回每个账号的结果摘要，供接口/日志展示。"""
    q = select(Account).where(Account.status == AccountStatus.ACTIVE.value)
    if user_id is not None:
        q = q.where(Account.user_id == user_id)
    accounts = list(session.exec(q).all())
    out = []
    for acc in accounts:
        works, err = collect_account(acc)
        saved = save_works(session, acc, works) if works else 0
        out.append({"account_id": acc.id, "account_name": acc.name,
                    "platform": acc.platform, "works": len(works), "saved": saved,
                    "error": err})
    return out


def recent_works(session: Session, user_id: int, limit: int = 30) -> list[dict]:
    """每个作品只取最新一天的数据，按播放量倒序。"""
    rows = session.exec(
        select(WorkStat).where(WorkStat.user_id == user_id)
        .order_by(WorkStat.stat_date.desc(), WorkStat.views.desc())
        .limit(limit * 3)
    ).all()
    seen, out = set(), []
    for r in rows:
        if r.work_id in seen:
            continue
        seen.add(r.work_id)
        acc = session.get(Account, r.account_id)
        out.append({
            "id": r.id, "account_id": r.account_id,
            "account_name": acc.name if acc else "",
            "platform": r.platform, "work_id": r.work_id,
            "work_url": r.work_url, "work_title": r.work_title,
            "stat_date": r.stat_date,
            "published_at": r.published_at.isoformat() if r.published_at else "",
            "views": r.views, "likes": r.likes, "comments": r.comments,
            "shares": r.shares, "favorites": r.favorites,
            "material_id": r.material_id,
        })
        if len(out) >= limit:
            break
    return out
