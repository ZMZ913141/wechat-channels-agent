"""发布审计 API：谁 / 哪个平台 / 什么素材 / 时间 / 结果（按用户行级隔离）。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session
from ...db import get_session
from ...models import User
from ...web.auth import get_current_user
from ...core import audit as audit_svc

router = APIRouter()

_VALID_PLATFORMS = ("wechat", "douyin", "xiaohongshu")


@router.get("")
def list_audit(
    platform: str | None = None,
    ok: bool | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if platform and platform not in _VALID_PLATFORMS:
        raise HTTPException(400, "未知平台")
    return audit_svc.list_audit(session, user.id, platform=platform, ok=ok, limit=limit)


@router.get("/summary")
def audit_summary(days: int = 7, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    if not 1 <= days <= 90:
        raise HTTPException(400, "days 需在 1~90 之间")
    return audit_svc.summarize(session, user.id, days=days)


@router.get("/export.csv")
def audit_export_csv(
    platform: str | None = None,
    ok: bool | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """导出审计流水 CSV（最多 5000 条），带 BOM，Excel 直接打开不乱码。"""
    if platform and platform not in _VALID_PLATFORMS:
        raise HTTPException(400, "未知平台")
    text = audit_svc.export_csv(session, user.id, platform=platform, ok=ok)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit_export.csv"'},
    )
