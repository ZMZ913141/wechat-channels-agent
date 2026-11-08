"""媒体文件服务：缩略图、视频预览（只读，防路径穿越，需登录）。"""
import mimetypes
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pathlib import Path
from sqlmodel import Session
from ...db import get_session
from ...models import User
from ...web.auth import get_current_user
from ...config import settings
from ...core import materials as mat_svc

router = APIRouter()


@router.get("/thumb/{name}")
def thumb(name: str, session: Session = Depends(get_session),
         user: User = Depends(get_current_user)):
    # 防路径穿越
    safe = Path(name).name
    p = settings.thumbs_dir / safe
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@router.get("/video/{mid}")
def video(mid: int, session: Session = Depends(get_session),
          user: User = Depends(get_current_user)):
    """素材原视频流式播放（支持 Range，浏览器可直接拖动进度条）。

    素材库「全部用户」视图下可以预览别人的素材（只读），所以这里只要求登录、
    不限制归属；本地单机工作台，媒体文件不对外网暴露。
    """
    m = mat_svc.get_material(session, mid)
    if not m:
        raise HTTPException(404, "素材不存在")
    p = Path(m.file_path)
    if not p.is_file():
        raise HTTPException(404, "视频文件已不存在（可能被移动或删除）")
    media_type = mimetypes.guess_type(p.name)[0] or "video/mp4"
    return FileResponse(p, media_type=media_type, headers={"Accept-Ranges": "bytes"})
