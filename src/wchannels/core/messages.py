"""私信采集：各平台账号的私信会话摘要（谁、最后一条、几条未读）。

与评论的关系：评论按作品逐条管理（可回复），私信以「采集+飞书提醒」为主。
视频号支持站外一键发送（/private-msg/send-private-msg，结构逆向自互动微前端）；
抖音 IM 是 protobuf 长连接、小红书接口带签名，这两个平台仍只做查看。
"""
from __future__ import annotations

from sqlmodel import Session, select

from ..browser.launcher import launch_context
from ..browser.platforms import get_publisher
from ..browser.proclock import AccountLock, ProfileLock, LockBusy
from ..config import settings
from ..models import Account, AccountStatus, PrivateMessage
from ..logutil import get_logger
from ..timeutils import now

log = get_logger(__name__)

PLATFORM_CN = {"wechat": "视频号", "douyin": "抖音", "xiaohongshu": "小红书"}


def collect_account(session: Session, account: Account) -> dict:
    """采集单个账号的私信会话摘要。返回结果 dict（new/error/samples）。"""
    pub = get_publisher(account.platform)
    if not hasattr(pub, "collect_messages"):
        return {"account_id": account.id, "account_name": account.name,
                "platform": account.platform, "new": 0,
                "error": f"{account.platform} 暂未支持私信采集", "samples": []}

    profile = settings.profiles_dir / account.profile_dir
    a_lock, p_lock = AccountLock(account.id), ProfileLock(profile)
    try:
        a_lock.acquire()
        p_lock.acquire()
    except LockBusy:
        log.info("跳过 %s：账号/profile 被占用", account.name)
        return {"account_id": account.id, "account_name": account.name,
                "platform": account.platform, "new": 0,
                "error": "账号正忙（浏览器被占用），稍后重试", "samples": []}
    try:
        # 与采集评论一致：有头但移出屏幕（纯无头会让登录态失效）
        with launch_context(profile, headless=False,
                            extra_args=["--window-position=-32000,-32000"]) as ctx:
            items, err = pub.collect_messages(ctx)
        new, samples = save_messages(session, account, items)
        return {"account_id": account.id, "account_name": account.name,
                "platform": account.platform, "new": new, "error": err,
                "samples": samples}
    except Exception as e:
        log.warning("私信采集 %s 失败：%s", account.name, e)
        return {"account_id": account.id, "account_name": account.name,
                "platform": account.platform, "new": 0, "error": str(e),
                "samples": []}
    finally:
        a_lock.release()
        p_lock.release()


def save_messages(session: Session, account: Account,
                  items: list[dict]) -> tuple[int, list[dict]]:
    """私信入库去重（msg_key）。返回 (新增数, 前几条样本)。"""
    if not items:
        return 0, []
    keys = [str(it.get("msg_key") or "") for it in items]
    existed_rows = {r.msg_key: r for r in session.exec(
        select(PrivateMessage)
        .where(PrivateMessage.account_id == account.id,
               PrivateMessage.msg_key.in_(keys))).all()}
    new_rows: list[PrivateMessage] = []
    for it, key in zip(items, keys):
        if not key:
            continue
        dup = existed_rows.get(key)
        if dup:
            # 已知会话：刷新多轮上下文和未读数（内容不变也补 history）
            new_hist = str(it.get("history") or "")
            if new_hist and new_hist != (dup.history or ""):
                dup.history = new_hist
                session.add(dup)
            new_unread = int(it.get("unread_count") or 0)
            if new_unread != dup.unread_count:
                dup.unread_count = new_unread
                session.add(dup)
            continue
        row = PrivateMessage(
            user_id=account.user_id, account_id=account.id,
            platform=account.platform,
            sender=str(it.get("sender") or "")[:40],
            content=str(it.get("content") or "")[:300],
            msg_key=key,
            session_id=str(it.get("session_id") or "")[:80],
            peer_username=str(it.get("peer_username") or "")[:80],
            unread_count=int(it.get("unread_count") or 0),
            is_group=bool(it.get("is_group")),
            created_text=str(it.get("created_text") or "")[:20],
            history=str(it.get("history") or ""),
        )
        session.add(row)
        new_rows.append(row)
    session.commit()
    samples = [{"sender": r.sender, "content": r.content,
                "unread_count": r.unread_count} for r in new_rows[:5]]
    return len(new_rows), samples


def list_messages(session: Session, user_id: int, account_id: int | None = None,
                  only_unread: bool = False, limit: int = 200) -> list[dict]:
    """私信列表（新采集的在前），附账号名。"""
    q = select(PrivateMessage).where(PrivateMessage.user_id == user_id)
    if account_id:
        q = q.where(PrivateMessage.account_id == account_id)
    if only_unread:
        q = q.where(PrivateMessage.unread_count > 0)
    rows = session.exec(q.order_by(PrivateMessage.id.desc()).limit(limit)).all()
    names = {a.id: a.name for a in session.exec(
        select(Account).where(Account.user_id == user_id)).all()}
    return [{
        "id": m.id, "account_id": m.account_id,
        "account_name": names.get(m.account_id, ""),
        "platform": m.platform, "sender": m.sender, "content": m.content,
        "unread_count": m.unread_count, "is_group": m.is_group,
        "created_text": m.created_text, "ai_draft": m.ai_draft,
        "can_send": bool(m.platform == "wechat" and m.session_id),
        "replied_at": m.replied_at.isoformat() if m.replied_at else "",
        "collected_at": m.collected_at.isoformat() if m.collected_at else "",
    } for m in rows]


def count_unread(session: Session, user_id: int) -> dict:
    rows = session.exec(
        select(PrivateMessage).where(PrivateMessage.user_id == user_id)).all()
    return {"total": len(rows),
            "unread": sum(1 for r in rows if r.unread_count > 0)}


def send_reply(session: Session, message_id: int, text: str,
               user_id: int | None = None) -> tuple[bool, str]:
    """一键发送私信回复（仅视频号）。成功返回 (True, "") 并记录 replied_at。"""
    m = session.get(PrivateMessage, message_id)
    if not m or (user_id is not None and m.user_id != user_id):
        return False, "私信不存在"
    if m.platform != "wechat":
        return False, f"{PLATFORM_CN.get(m.platform, m.platform)} 暂不支持站外发送，请到平台 App 回复"
    if not m.session_id:
        return False, "这条私信是旧数据，缺少会话 ID：请点「抓取新私信」重新采集后再发送"
    text = (text or "").strip()
    if not text:
        return False, "回复内容不能为空"
    account = session.get(Account, m.account_id)
    if not account:
        return False, "账号不存在"
    if account.status != AccountStatus.ACTIVE.value:
        return False, "账号不在登录状态，请先到「账号」页重新登录"

    pub = get_publisher(account.platform)
    if not hasattr(pub, "send_private_message"):
        return False, "该平台暂不支持站外发送"
    profile = settings.profiles_dir / account.profile_dir
    a_lock, p_lock = AccountLock(account.id), ProfileLock(profile)
    try:
        a_lock.acquire()
        p_lock.acquire()
    except LockBusy:
        return False, "账号正忙（浏览器被占用），稍后重试"
    try:
        with launch_context(profile, headless=False,
                            extra_args=["--window-position=-32000,-32000"]) as ctx:
            ok, err = pub.send_private_message(ctx, m.session_id,
                                               m.peer_username, text)
        if ok:
            m.replied_at = now()
            m.ai_draft = text
            session.add(m)
            session.commit()
            return True, ""
        return False, err
    except Exception as e:
        log.warning("私信发送失败（%s）：%s", account.name, e)
        return False, f"发送过程出错：{e}"
    finally:
        a_lock.release()
        p_lock.release()


def collect_all(session: Session, user_id: int | None = None,
                notify_new: bool = True) -> list[dict]:
    """采集所有 active 账号的私信；有新私信时发飞书通知（含内容摘要）。"""
    q = select(Account).where(Account.status == AccountStatus.ACTIVE.value)
    if user_id is not None:
        q = q.where(Account.user_id == user_id)
    accounts = list(session.exec(q).all())
    results = [collect_account(session, a) for a in accounts]

    if notify_new:
        fresh = [r for r in results if r["new"]]
        if fresh:
            from . import notify
            lines: list[str] = []
            for r in fresh:
                plat = PLATFORM_CN.get(r["platform"], r["platform"])
                lines.append(f"{r['account_name']}（{plat}）：{r['new']} 条新私信")
                for s in r.get("samples", [])[:5]:
                    content = (s.get("content") or "").replace("\n", " ")[:30]
                    unread = s.get("unread_count") or 0
                    tag = f"（{unread} 条未读）" if unread else ""
                    lines.append(f"· {s.get('sender') or '未知'}：{content}{tag}")
            lines.append("请到各平台 App 查看并回复私信")
            notify.send("收到新私信", lines, level="info")
    return results
