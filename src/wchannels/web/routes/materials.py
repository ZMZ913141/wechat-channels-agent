"""素材库 API（已接入多用户，按 user_id 行级隔离）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from ...db import get_session
from ...models import User, Material, MaterialStatus, Account, PublishTarget
from ...web.auth import get_current_user
from ...core import materials as svc
from ...core import material_account_configs as mac_svc
from ...ai import autofill
from ...config import settings
from pathlib import Path
from fastapi import UploadFile, File

router = APIRouter()


@router.get("")
def list_materials(status: str | None = None, keyword: str | None = None,
                   tag: str | None = None, scope: str = "mine",
                   page: int = 1, size: int = 24,
                   session: Session = Depends(get_session),
                   user: User = Depends(get_current_user)):
    items, total = svc.list_materials(session, user.id, status=status, keyword=keyword, tag=tag,
                                      page=page, size=size, scope=scope)
    owners: dict[int, str] = {}
    if items:
        ids = {m.user_id for m in items}
        for u in session.exec(select(User).where(User.id.in_(ids))).all():
            owners[u.id] = u.display_name or u.username
    return {
        "items": [_serialize(m, owner_name=owners.get(m.user_id, "")) for m in items],
        "total": total, "page": page, "size": size,
    }


@router.get("/tags")
def material_tags(scope: str = "mine", session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    """素材库里出现过的分区标签，供顶部按标签分区筛选。"""
    return {"tags": svc.distinct_tags(session, user.id, scope=scope)}


@router.get("/owners")
def material_owners(session: Session = Depends(get_session),
                    user: User = Depends(get_current_user)):
    """素材归属人列表（含各自数量），供「全部用户」下再按某个用户筛选。"""
    from sqlalchemy import func
    rows = session.exec(
        select(Material.user_id, func.count()).group_by(Material.user_id)
    ).all()
    names = {u.id: (u.display_name or u.username) for u in
             session.exec(select(User).where(User.id.in_({r[0] for r in rows}))).all()}
    return {"owners": [
        {"user_id": uid, "name": names.get(uid, "未知用户"), "count": int(cnt)}
        for uid, cnt in sorted(rows, key=lambda r: -r[1])
    ]}


@router.post("/scan-inbox")
def scan_inbox(session: Session = Depends(get_session),
              user: User = Depends(get_current_user)):
    added = svc.scan_inbox(session, user.id)
    return {"added": len(added), "items": [_serialize(m) for m in added]}


class AutofillBody(BaseModel):
    extra_hint: str = ""
    platform: str = "wechat"
    transcript: str = ""      # 用户粘贴的视频文案/口播稿，有值时以其为核心依据
    dry_run: bool = False     # True=只返回文案给前端填入表单，不改库、不改状态


@router.post("/{mid}/autofill")
def autofill_material(mid: int, body: AutofillBody, session: Session = Depends(get_session),
                      user: User = Depends(get_current_user)):
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    if body.platform not in settings.PLATFORMS:
        raise HTTPException(400, f"未知平台：{body.platform}")
    # 按目标平台生成文案（此前恒用 wechat，README 宣称的"按平台风格"实际没接上）
    copy = autofill.generate_copy(
        Path(m.file_path).name,
        extra_hint=body.extra_hint,
        platform=body.platform,
        title_hint=m.short_title or m.title,
        description_hint=m.description,
        transcript=body.transcript,
    )
    if body.dry_run:
        # 配置面板「AI 辅助填写」：只把结果填进表单让用户确认，不自动保存/转状态
        return {"dry_run": True, "copy": copy}
    m = svc.update_material(
        session, m,
        short_title=copy["short_title"],
        description=copy["description"],
        topics=copy["topics"],
    )
    # 小白最容易卡在「扫完素材 → 去队列发现勾不到」：文案齐了就直接转待发布
    if m.status == MaterialStatus.INBOX.value:
        m = svc.to_ready(session, m)
    m._ai_used = copy.get("ai_used", False)  # 临时标记，供序列化读取
    return _serialize(m, ai_used=copy.get("ai_used", False))


@router.post("/upload")
async def upload_material(file: UploadFile = File(...), session: Session = Depends(get_session),
                          user: User = Depends(get_current_user)):
    """浏览器上传素材：文件落盘到 inbox 后自动入库。无需手动丢文件夹。"""
    import tempfile
    original = Path(file.filename or "video.mp4").name
    suffix = Path(original).suffix or ".mp4"
    if suffix.lower() not in svc.VIDEO_EXTS:
        raise HTTPException(400, f"不支持 {suffix} 格式，请上传 mp4 / mov / avi / mkv / webm 视频")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        # 先判断重复：提示要能分清「格式不对」和「已经传过」
        if svc.has_hash(session, user.id, svc.file_hash(tmp_path)):
            raise HTTPException(400, f"「{original}」这个视频已经在素材库里了，不用重复上传")
        m = svc.import_upload(session, user.id, tmp_path, original_name=original)
    finally:
        tmp_path.unlink(missing_ok=True)
    if not m:
        raise HTTPException(400, "导入失败，请确认文件是完整的视频")
    return _serialize(m)


class UpdateBody(BaseModel):
    title: str | None = None
    short_title: str | None = None
    description: str | None = None
    topics: str | None = None
    ai_disclosure: str | None = None
    collection: str | None = None
    cover_path: str | None = None
    tags: str | None = None


@router.put("/{mid}")
def update_material(mid: int, body: UpdateBody, session: Session = Depends(get_session),
                   user: User = Depends(get_current_user)):
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if "tags" in data:
        # 统一成半角逗号 + 去空格，保证筛选时能按逗号精确匹配
        parts = [t.strip() for t in str(data["tags"]).replace("，", ",").split(",")]
        data["tags"] = ",".join([t for t in parts if t])
    m = svc.update_material(session, m, **data)
    return _serialize(m)


@router.post("/{mid}/to-ready")
def to_ready(mid: int, session: Session = Depends(get_session),
             user: User = Depends(get_current_user)):
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    m = svc.to_ready(session, m)
    return _serialize(m)


@router.delete("/{mid}")
def trash(mid: int, session: Session = Depends(get_session),
          user: User = Depends(get_current_user)):
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    svc.move_trash(session, m)
    return {"ok": True}


@router.delete("/{mid}/hard")
def hard_delete(mid: int, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    """彻底删除：素材记录 + 本地视频文件（含缩略图/封面）一起删掉，不可恢复。"""
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    try:
        removed = svc.purge(session, m)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "removed_file": removed["file"]}


@router.post("/{mid}/restore")
def restore(mid: int, session: Session = Depends(get_session),
            user: User = Depends(get_current_user)):
    """回收站恢复：core 层早有 restore()，但没有路由调用它，误删素材界面回不来。"""
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    if m.status != MaterialStatus.TRASH.value:
        raise HTTPException(400, "该素材不在回收站")
    m = svc.restore(session, m)
    return _serialize(m)


# ---------- 素材 × 账号 独立配置 ----------

class AccountConfigBody(BaseModel):
    short_title: str | None = None
    description: str | None = None
    topics: str | None = None
    collection: str | None = None
    ai_disclosure: str | None = None
    cover_path: str | None = None
    original_work: bool | None = None
    comment: str | None = None
    visibility: str | None = None


@router.get("/{mid}/account-configs")
def list_account_configs(mid: int, session: Session = Depends(get_session),
                         user: User = Depends(get_current_user)):
    """返回该素材在每个账号下的发布配置（没配过的账号也返回空配置，方便前端直接编辑）。"""
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    accounts = list(session.exec(select(Account).where(Account.user_id == user.id)).all())
    cfgs = {c.account_id: c for c in mac_svc.list_for_material(session, mid)}
    # 每个号最近一次发布结果：用来区分「这个号已发过」和「还没发」
    last: dict[int, PublishTarget] = {}
    for t in session.exec(select(PublishTarget).where(PublishTarget.material_id == mid)
                          .order_by(PublishTarget.id.desc())).all():
        last.setdefault(t.account_id, t)
    out = []
    for a in accounts:
        c = cfgs.get(a.id)
        t = last.get(a.id)
        out.append({
            "account_id": a.id,
            "account_name": a.name,
            "platform": a.platform,
            "status": a.status,
            "publish_status": t.status if t else "",
            "published_at": t.published_at.isoformat() if t and t.published_at else "",
            "publish_message": t.result_message if t else "",
            "short_title": c.short_title if c else "",
            "description": c.description if c else "",
            "topics": c.topics if c else "",
            "collection": c.collection if c else "",
            "ai_disclosure": c.ai_disclosure if c else "",
            "cover_path": c.cover_path if c else "",
            "original_work": c.original_work if c else False,
            "comment": c.comment if c else "",
            "visibility": (c.visibility if c and c.visibility else "public"),
        })
    return out


@router.put("/{mid}/account-configs/{aid}")
def put_account_config(mid: int, aid: int, body: AccountConfigBody,
                       session: Session = Depends(get_session),
                       user: User = Depends(get_current_user)):
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    a = session.get(Account, aid)
    if not a or a.user_id != user.id:
        raise HTTPException(404, "账号不存在")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if data.get("visibility") not in (None, "public", "private"):
        raise HTTPException(400, "可见范围只能是 public（公开）或 private（仅自己可见）")
    # 原创按素材全局互斥：已有其他账号勾了原创，本账号就不能再勾
    if data.get("original_work") and mac_svc.has_other_original(session, mid, aid):
        raise HTTPException(400, "这条视频已有其他账号勾选「原创」，同一个视频只能有一个账号声明原创")
    try:
        cfg = mac_svc.upsert(session, mid, aid, **data)
    except IntegrityError:
        # 数据库唯一索引兜底（并发保存时两个请求都通过了上面的检查）
        session.rollback()
        raise HTTPException(400, "这条视频已有其他账号勾选「原创」，同一个视频只能有一个账号声明原创")
    return {"ok": True, "id": cfg.id}


class MaterialStatusBody(BaseModel):
    status: str


@router.post("/{mid}/status")
def set_material_status(mid: int, body: MaterialStatusBody,
                        session: Session = Depends(get_session),
                        user: User = Depends(get_current_user)):
    """手动改状态：误点「AI 完善/加入待发布」后可以退回待处理，也能改回待发布。"""
    m = svc.get_material(session, mid, user.id)
    if not m:
        raise HTTPException(404, "素材不存在")
    if body.status not in (MaterialStatus.INBOX.value, MaterialStatus.READY.value,
                           MaterialStatus.PUBLISHED.value):
        raise HTTPException(400, "状态只能是 inbox / ready / published")
    svc.update_material(session, m, status=body.status)
    return {"ok": True, "status": m.status}


def _serialize(m, ai_used: bool | None = None, owner_name: str = "") -> dict:
    thumb_url = ""
    if m.thumbnail_path:
        thumb_url = "/api/media/thumb/" + Path(m.thumbnail_path).name
    return {
        "id": m.id,
        "user_id": m.user_id,
        "owner_name": owner_name,
        "tags": m.tags,
        "file_path": m.file_path,
        "file_name": Path(m.file_path).name,
        "file_size": m.file_size,
        "duration_sec": m.duration_sec,
        "width": m.width,
        "height": m.height,
        "title": m.title,
        "short_title": m.short_title,
        "description": m.description,
        "topics": m.topics,
        "ai_disclosure": m.ai_disclosure,
        "collection": m.collection,
        "cover_path": m.cover_path,
        "status": m.status,
        "thumbnail_path": m.thumbnail_path,
        "thumb_url": thumb_url,
        "ai_used": ai_used if ai_used is not None else bool(getattr(m, "_ai_used", False)),
    }
