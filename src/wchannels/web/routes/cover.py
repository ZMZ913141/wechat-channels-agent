"""封面管理：从视频抽帧、上传封面、裁剪成视频号比例（已接入多用户）。"""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlmodel import Session
from pathlib import Path
import subprocess
from ...db import get_session
from ...models import User
from ...web.auth import get_current_user
from ...core import materials as mat_svc
from ...config import settings

router = APIRouter()


@router.post("/{mid}/extract-frame")
def extract_frame(mid: int, at_sec: float = 1.0, session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    """从视频指定时间点抽帧，设为封面。"""
    m = mat_svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    out = settings.thumbs_dir / f"cover_{mid}.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", m.file_path, "-ss", str(at_sec),
             "-vframes", "1", "-q:v", "2", str(out)],
            capture_output=True, timeout=30,
        )
        if out.exists():
            m = mat_svc.update_material(session, m, cover_path=str(out))
            return {"ok": True, "cover": str(out)}
    except Exception as e:
        raise HTTPException(500, f"抽帧失败：{e}")
    raise HTTPException(500, "抽帧失败")


@router.post("/{mid}/upload-cover")
async def upload_cover(mid: int, file: UploadFile = File(...), session: Session = Depends(get_session),
                      user: User = Depends(get_current_user)):
    """用户上传封面图。"""
    m = mat_svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    out = settings.thumbs_dir / f"cover_{mid}.jpg"
    content = await file.read()
    out.write_bytes(content)
    m = mat_svc.update_material(session, m, cover_path=str(out))
    return {"ok": True, "cover": str(out)}
