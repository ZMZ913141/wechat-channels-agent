"""看板统计聚合。"""
from sqlmodel import Session, select, func
from datetime import datetime, timedelta
from ..models import Material, PublishTarget, TargetStatus, Account, PublishBatch, WorkStat
from ..timeutils import now as utcnow


def dashboard(session: Session, user_id: int) -> dict:
    week_ago = utcnow() - timedelta(days=7)

    total_materials = session.exec(
        select(func.count(Material.id)).where(Material.user_id == user_id)
    ).one()
    inbox_count = session.exec(
        select(func.count(Material.id)).where(
            Material.user_id == user_id, Material.status == "inbox"
        )
    ).one()
    ready_count = session.exec(
        select(func.count(Material.id)).where(
            Material.user_id == user_id, Material.status == "ready"
        )
    ).one()

    # 真·本周：按批次创建时间过滤。此前 week_ago 算了但没进查询条件，
    # 「本周发布」实际是全时段累计，小白对不上数会以为统计坏了。
    week_targets = list(session.exec(
        select(PublishTarget)
        .join(Account, PublishTarget.account_id == Account.id)
        .join(PublishBatch, PublishTarget.batch_id == PublishBatch.id)
        .where(
            Account.user_id == user_id,
            PublishBatch.created_at >= week_ago,
        )
    ).all())
    total = len(week_targets)
    success = sum(1 for t in week_targets if t.status == TargetStatus.SUCCESS.value)
    failed = sum(1 for t in week_targets if t.status == TargetStatus.FAILED.value)
    rate = round(success / total * 100, 1) if total else 0

    # 各账号本周发布数
    acc_stats = {}
    for t in week_targets:
        acc = session.get(Account, t.account_id)
        if acc:
            acc_stats[acc.name] = acc_stats.get(acc.name, 0) + (1 if t.status == TargetStatus.SUCCESS.value else 0)

    # 互动待办：新评论数 + 未读私信会话数（今日页聚合条用）
    from . import comments as comments_svc
    from . import messages as messages_svc
    c_counts = comments_svc.count_by_status(session, user_id)
    m_counts = messages_svc.count_unread(session, user_id)

    return {
        "materials": {"total": total_materials, "inbox": inbox_count, "ready": ready_count},
        "week": {"total": total, "success": success, "failed": failed, "success_rate": rate},
        "by_account": acc_stats,
        "interaction": {"new_comments": c_counts["new"],
                        "unread_messages": m_counts["unread"]},
    }


def trend(session: Session, user_id: int, days: int = 14) -> dict:
    """作品数据趋势 + 账号对比（看板图表用）。

    WorkStat 是「每作品每天一条」的快照，数值本身是平台累计口径：
    - 趋势：把所有作品当天的数值加总，看整体走势；
    - 账号对比：每个作品只取采集到的最新一天再求和，避免重复计数。
    """
    since = (utcnow() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = list(session.exec(
        select(WorkStat).where(
            WorkStat.user_id == user_id,
            WorkStat.stat_date >= since,
        )
    ).all())

    # 按天加总（补齐没有采集的日期，图表不断线）
    by_day: dict[str, dict] = {}
    for i in range(days):
        d = (utcnow() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        by_day[d] = {"date": d[5:], "views": 0, "likes": 0}
    for r in rows:
        slot = by_day.get(r.stat_date)
        if slot:
            slot["views"] += r.views
            slot["likes"] += r.likes

    # 账号对比：同作品只留最新一天
    latest: dict[tuple[int, str], WorkStat] = {}
    for r in rows:
        key = (r.account_id, r.work_id)
        cur = latest.get(key)
        if not cur or r.stat_date > cur.stat_date:
            latest[key] = r
    acc_map: dict[int, dict] = {}
    for r in latest.values():
        a = acc_map.get(r.account_id)
        if not a:
            acc = session.get(Account, r.account_id)
            a = acc_map[r.account_id] = {
                "name": acc.name if acc else f"账号{r.account_id}",
                "platform": r.platform, "views": 0, "likes": 0,
            }
        a["views"] += r.views
        a["likes"] += r.likes

    return {
        "days": list(by_day.values()),
        "accounts": sorted(acc_map.values(), key=lambda x: x["views"], reverse=True),
    }
