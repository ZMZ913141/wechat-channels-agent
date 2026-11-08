"""发布审计：记录每次发布尝试，并提供按用户隔离的查询。

写入只做 INSERT，绝不修改既有记录——审计的价值在于不可回溯篡改。
"""
import csv
import io
from sqlmodel import Session, select, delete
from datetime import timedelta
from ..models import PublishAudit, AuditSource, Account, Material
from ..timeutils import now as utcnow

_PLATFORM_CN = {"wechat": "视频号", "douyin": "抖音", "xiaohongshu": "小红书"}
_SOURCE_CN = {"manual": "手动", "scheduler": "自动", "skipped": "跳过"}


def record(
    session: Session,
    *,
    user_id: int,
    account_id: int,
    material_id: int,
    platform: str,
    ok: bool,
    message: str,
    source: str = AuditSource.MANUAL.value,
    target_id: int | None = None,
    batch_id: int | None = None,
    screenshot: str = "",
    duration_ms: int = 0,
) -> PublishAudit:
    """落一条审计流水。独立提交，避免与发布主流程的事务失败互相牵连。"""
    row = PublishAudit(
        user_id=user_id,
        account_id=account_id,
        material_id=material_id,
        platform=platform,
        ok=ok,
        message=(message or "")[:2000],
        source=source,
        target_id=target_id,
        batch_id=batch_id,
        screenshot=screenshot,
        duration_ms=max(int(duration_ms or 0), 0),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_audit(
    session: Session,
    user_id: int,
    *,
    platform: str | None = None,
    ok: bool | None = None,
    limit: int = 100,
) -> list[dict]:
    """查询当前用户的审计流水（行级隔离），附带账号名与素材名。"""
    q = select(PublishAudit).where(PublishAudit.user_id == user_id)
    if platform:
        q = q.where(PublishAudit.platform == platform)
    if ok is not None:
        q = q.where(PublishAudit.ok == ok)
    q = q.order_by(PublishAudit.created_at.desc()).offset(0).limit(max(1, min(limit, 500)))

    rows = list(session.exec(q).all())
    out = []
    for r in rows:
        acc = session.get(Account, r.account_id)
        mat = session.get(Material, r.material_id)
        out.append({
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "account": acc.name if acc else "",
            "platform": r.platform,
            "material": (mat.title or mat.short_title) if mat else "",
            "ok": r.ok,
            "status": "成功" if r.ok else "失败",
            "source": r.source,
            "message": r.message,
            "screenshot": r.screenshot,
            "duration_ms": r.duration_ms,
            "batch_id": r.batch_id,
            "target_id": r.target_id,
        })
    return out


def summarize(session: Session, user_id: int, days: int = 7) -> dict:
    """近 N 天审计汇总：总次数、成功率、按平台分布、最近失败原因。"""
    since = utcnow() - timedelta(days=days)
    rows = list(session.exec(
        select(PublishAudit).where(
            PublishAudit.user_id == user_id, PublishAudit.created_at >= since
        )
    ).all())
    total = len(rows)
    success = sum(1 for r in rows if r.ok)
    by_platform: dict[str, dict] = {}
    for r in rows:
        b = by_platform.setdefault(r.platform or "unknown", {"total": 0, "success": 0})
        b["total"] += 1
        if r.ok:
            b["success"] += 1
    failures = [
        {"time": r.created_at.isoformat() if r.created_at else None,
         "platform": r.platform, "message": r.message}
        for r in sorted(rows, key=lambda x: x.created_at, reverse=True) if not r.ok
    ][:10]
    return {
        "days": days,
        "total": total,
        "success": success,
        "failed": total - success,
        "success_rate": round(success / total * 100, 1) if total else 0,
        "by_platform": by_platform,
        "recent_failures": failures,
    }


def export_csv(
    session: Session,
    user_id: int,
    *,
    platform: str | None = None,
    ok: bool | None = None,
    limit: int = 5000,
) -> str:
    """导出当前用户的审计流水为 CSV 文本（utf-8-sig，Excel 直接打开不乱码）。"""
    rows = list_audit(session, user_id, platform=platform, ok=ok, limit=limit)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["时间", "账号", "平台", "素材", "结果", "来源", "耗时(秒)", "说明"])
    for r in rows:
        w.writerow([
            r["created_at"] or "",
            r["account"],
            _PLATFORM_CN.get(r["platform"], r["platform"]),
            r["material"],
            "成功" if r["ok"] else "失败",
            _SOURCE_CN.get(r["source"], r["source"]),
            round(r["duration_ms"] / 1000, 1) if r["duration_ms"] else "",
            (r["message"] or "").replace("\r", " ").replace("\n", " "),
        ])
    return "\ufeff" + buf.getvalue()


def purge_older_than(session: Session, days: int) -> int:
    """清理 N 天前的审计记录（全表共享保留期），返回删除条数。days<=0 不清理。"""
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    result = session.exec(delete(PublishAudit).where(PublishAudit.created_at < cutoff))
    session.commit()
    return result.rowcount or 0
