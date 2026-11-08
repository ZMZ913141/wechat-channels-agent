"""评论采集与落库：把平台评论抓回本地，供 AI 草稿/回复/通知使用。"""
import hashlib

from sqlmodel import Session, select

from ..browser.launcher import launch_context
from ..browser.platforms import collect_comments_for_account, get_publisher
from ..browser.proclock import AccountLock, ProfileLock, LockBusy
from ..config import settings
from ..models import Account, AccountStatus, Comment, WorkStat
from ..logutil import get_logger
from ..timeutils import now

log = get_logger("comments")
PLATFORM_CN = {"wechat": "视频号", "douyin": "抖音", "xiaohongshu": "小红书"}


def _fingerprint(item: dict) -> str:
    """平台评论 ID 优先；拿不到 ID 时用「作者+内容+时间」指纹去重。"""
    cid = (item.get("comment_id") or "").strip()
    if cid:
        return cid
    raw = "|".join([item.get("author") or "", item.get("content") or "",
                    item.get("created_text") or ""])
    return "c" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def latest_works(session: Session, account: Account) -> list[dict]:
    """每个作品取最新一天的快照，只保留可能有评论的作品（减少点开次数）。"""
    rows = session.exec(
        select(WorkStat).where(WorkStat.account_id == account.id)
        .order_by(WorkStat.stat_date.desc())
    ).all()
    seen, out = set(), []
    for r in rows:
        if r.work_id in seen:
            continue
        seen.add(r.work_id)
        out.append({"work_id": r.work_id, "work_title": r.work_title,
                    "work_url": r.work_url, "comments": r.comments})
    return out


def save_comments(session: Session, account: Account, work: dict,
                  items: list[dict]) -> list[Comment]:
    """按指纹去重落库，返回本次新发现的评论。"""
    new_rows: list[Comment] = []
    wid = work.get("work_id") or ""
    for it in items:
        content = (it.get("content") or "").strip()
        if not content:
            continue
        key = _fingerprint(it)
        exists = session.exec(
            select(Comment).where(Comment.account_id == account.id,
                                  Comment.comment_key == key)
        ).first()
        if exists:
            # 作品数据先采到位时，这里能把早期评论补挂到正确作品上
            changed = False
            if wid and exists.work_id != wid:
                exists.work_id = wid
                changed = True
            title = (work.get("work_title") or "")[:80]
            if title and exists.work_title != title:
                exists.work_title = title
                changed = True
            if changed:
                session.add(exists)
            continue
        row = Comment(
            user_id=account.user_id, account_id=account.id,
            platform=account.platform, work_id=wid,
            work_title=(work.get("work_title") or "")[:80],
            work_url=work.get("work_url") or "",
            comment_key=key,
            parent_key=(it.get("parent_key") or "")[:64],
            author=(it.get("author") or "观众")[:40],
            content=content[:1000],
            created_text=(it.get("created_text") or "")[:40],
            is_author=bool(it.get("is_author")),
            status="new",
        )
        session.add(row)
        new_rows.append(row)
    session.commit()
    return new_rows


def collect_account(session: Session, account: Account) -> dict:
    """采集单个账号的评论。返回摘要；失败不抛异常。"""
    # 评论接口自己会返回作品列表，本地只用来把评论挂到正确的作品上，
    # 所以没采过作品数据也能采评论（work_id 会退回平台返回的 ID）。
    works = latest_works(session, account)
    profile = settings.profiles_dir / account.profile_dir
    a_lock, p_lock = AccountLock(account.id), ProfileLock(profile)
    try:
        a_lock.acquire()
        p_lock.acquire()
    except LockBusy:
        return {"account_id": account.id, "account_name": account.name,
                "platform": account.platform, "new": 0,
                "error": "账号正忙（有发布任务在执行），稍后再采"}

    total_new = 0
    err = ""
    samples: list[dict] = []  # 通知里展示用的新评论摘要（最多 5 条）
    try:
        # 视频号对纯无头模式会主动让登录态失效，这里用「有头但移出屏幕」的窗口
        with launch_context(profile, headless=False,
                            extra_args=["--window-position=-32000,-32000"]) as ctx:
            items_by_work, err = collect_comments_for_account(ctx, account, works)
            for wid, (work, items) in items_by_work.items():
                rows = save_comments(session, account, work, items)
                total_new += len(rows)
                for r in rows:
                    if len(samples) < 5:
                        samples.append({"author": r.author, "content": r.content})
    except Exception as e:
        err = f"采集失败：{e}"
        log.warning("评论采集 %s 失败：%s", account.name, e)
    finally:
        a_lock.release()
        p_lock.release()
    return {"account_id": account.id, "account_name": account.name,
            "platform": account.platform, "new": total_new, "error": err,
            "samples": samples}


def send_reply(session: Session, comment_id: int, text: str,
               user_id: int | None = None) -> tuple[bool, str]:
    """一键回复：把文本发到平台上对应的评论下。成功返回 (True, "")。"""
    text = (text or "").strip()
    if not text:
        return False, "回复内容不能为空"
    c = session.get(Comment, comment_id)
    if not c or (user_id and c.user_id != user_id):
        return False, "评论不存在"
    acc = session.get(Account, c.account_id)
    if not acc or acc.status != "active":
        return False, "账号不可用"
    pub = get_publisher(acc.platform)
    if not hasattr(pub, "reply_comment"):
        return False, f"{acc.platform} 暂不支持一键回复"
    profile = settings.profiles_dir / acc.profile_dir
    a_lock, p_lock = AccountLock(acc.id), ProfileLock(profile)
    try:
        a_lock.acquire()
        p_lock.acquire()
    except LockBusy:
        return False, "账号正忙（有任务在执行），稍后再试"
    try:
        # 与采集一致：有头但移出屏幕（纯无头会让视频号登录态失效）
        with launch_context(profile, headless=False,
                            extra_args=["--window-position=-32000,-32000"]) as ctx:
            ok, err = pub.reply_comment(ctx, {
                "work_id": c.work_id, "work_title": c.work_title,
                "work_url": c.work_url, "comment_key": c.comment_key,
                "author": c.author, "content": c.content,
            }, text)
        if not ok:
            return False, err or "回复发送失败"
        c.status = "replied"
        c.replied_at = c.replied_at or now()
        c.ai_draft = text
        session.add(c)
        session.commit()
        return True, ""
    except Exception as e:
        log.warning("一键回复 %s 失败：%s", acc.name, e)
        return False, f"回复发送失败：{e}"
    finally:
        a_lock.release()
        p_lock.release()


def collect_all(session: Session, user_id: int | None = None,
                notify_new: bool = True) -> list[dict]:
    """采集所有 active 账号；有新评论时发飞书通知。"""
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
                lines.append(f"{r['account_name']}（{plat}）：{r['new']} 条新评论")
                for s in r.get("samples", [])[:5]:
                    content = (s.get("content") or "").replace("\n", " ")[:30]
                    lines.append(f"· {s.get('author') or '观众'}：{content}")
            lines.append("到「评论管理」查看并回复")
            notify.send("收到新评论", lines, level="info")
    return results


def list_comments(session: Session, user_id: int, status: str = "",
                  account_id: int | None = None, limit: int = 50) -> list[dict]:
    q = select(Comment).where(Comment.user_id == user_id)
    if status:
        q = q.where(Comment.status == status)
    if account_id:
        q = q.where(Comment.account_id == account_id)
    rows = session.exec(q.order_by(Comment.id.desc()).limit(limit)).all()
    names = {a.id: a.name for a in session.exec(
        select(Account).where(Account.user_id == user_id)).all()}
    return [{
        "id": c.id, "account_id": c.account_id,
        "account_name": names.get(c.account_id, ""),
        "platform": c.platform, "work_id": c.work_id,
        "work_title": c.work_title, "work_url": c.work_url,
        "author": c.author, "content": c.content,
        "parent_key": c.parent_key, "is_author": c.is_author,
        "created_text": c.created_text,
        "status": c.status, "ai_draft": c.ai_draft,
        "collected_at": c.collected_at.isoformat() if c.collected_at else "",
        "replied_at": c.replied_at.isoformat() if c.replied_at else "",
    } for c in rows]


def count_by_status(session: Session, user_id: int) -> dict:
    rows = session.exec(select(Comment).where(Comment.user_id == user_id)).all()
    out = {"new": 0, "drafted": 0, "replied": 0, "ignored": 0, "total": 0}
    for c in rows:
        out["total"] += 1
        if c.status in out:
            out[c.status] += 1
    return out
