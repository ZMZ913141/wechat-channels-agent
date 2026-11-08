"""素材库：inbox 扫描入库、去重、ffprobe 探测、状态流转、缩略图。"""
from sqlmodel import Session, select
from sqlalchemy import func
from pathlib import Path
from datetime import datetime
import hashlib
import subprocess
import json
from ..models import Material, MaterialStatus, Tag, MaterialTag
from ..config import settings
from ..timeutils import now


def _file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


file_hash = _file_hash  # 供路由层在上传前查重


def _probe_video(path: Path) -> dict:
    """用 ffprobe 读视频规格。失败返回空 dict。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",  # 同上：Windows GBK 默认会解码失败
        )
        data = json.loads(r.stdout)
        vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        return {
            "duration_sec": float(data.get("format", {}).get("duration", 0)),
            "width": int(vstream.get("width", 0)),
            "height": int(vstream.get("height", 0)),
        }
    except Exception:
        return {}


_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")
VIDEO_EXTS = _VIDEO_EXTS  # 供路由层给出更明确的报错


def has_hash(session: Session, user_id: int, file_hash: str) -> bool:
    """该用户素材库里是否已有相同内容的文件（用于上传前给出「已存在」提示）。"""
    return session.exec(
        select(Material).where(Material.user_id == user_id, Material.file_hash == file_hash)
    ).first() is not None


def _import_one(session: Session, user_id: int, src: Path, title_name: str | None = None) -> Material | None:
    """把一个视频文件入库（去重 + ffprobe 探测）。已在库则跳过。

    title_name: 卡片上显示的标题来源（浏览器上传时传原始文件名，避免显示临时随机名）。
    """
    if not src.is_file() or src.suffix.lower() not in _VIDEO_EXTS:
        return None
    h = _file_hash(src)
    if has_hash(session, user_id, h):
        return None
    probe = _probe_video(src)
    mat = Material(
        user_id=user_id,
        file_path=str(src.resolve()),
        file_hash=h,
        file_size=src.stat().st_size,
        title=Path(title_name or src.name).stem,
        duration_sec=probe.get("duration_sec", 0),
        width=probe.get("width", 0),
        height=probe.get("height", 0),
        status=MaterialStatus.INBOX.value,
    )
    session.add(mat)
    session.flush()
    session.refresh(mat)
    # 入库即抽首帧，素材库能看画面（ffmpeg 缺失/失败则留空，不阻断入库）
    try:
        thumb = generate_thumbnail(mat)
        if thumb:
            mat.thumbnail_path = thumb
            session.add(mat)
    except Exception:
        pass
    return mat


def scan_inbox(session: Session, user_id: int) -> list[Material]:
    """扫描 inbox 目录，把新文件入库为 inbox 状态。"""
    inbox = settings.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    added = []
    for f in inbox.iterdir():
        m = _import_one(session, user_id, f)
        if m:
            added.append(m)
    session.commit()
    for m in added:
        session.refresh(m)
    return added


def import_upload(session: Session, user_id: int, src: Path, original_name: str | None = None) -> Material | None:
    """接收浏览器上传的文件：先落盘到 inbox 目录，再入库。

    这是「素材导入」的正规入口——不必再把文件手动丢进文件夹。
    original_name: 浏览器传上来的原始文件名（上传中间文件是随机临时名，不能用来当标题）。
    返回导入的素材（重复/非视频返回 None）。
    """
    inbox = settings.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    display = Path(original_name or src.name).name
    # 防止覆盖：用时间戳前缀
    safe_name = f"{now().strftime('%Y%m%d_%H%M%S')}_{display}"
    dst = inbox / safe_name
    import shutil
    shutil.copy2(src, dst)
    m = _import_one(session, user_id, dst, title_name=display)
    session.commit()
    if m:
        session.refresh(m)
    return m


def list_materials(
    session: Session, user_id: int,
    status: str | None = None, tag: str | None = None,
    keyword: str | None = None,
    page: int = 1, size: int = 24,
    scope: str = "mine",
) -> tuple[list[Material], int]:
    """返回 (当前页素材列表, 总数)。素材量大时分页避免一次渲染几百张卡片卡顿。

    scope: mine=只看本人（默认）；all=所有用户（用于「归属人」筛选）；数字=指定用户 id。
    tag: 按分区标签过滤（逗号分隔字段里的包含匹配）。
    """
    q = select(Material)
    if scope == "all":
        pass
    elif scope.isdigit() and int(scope) != user_id:
        q = q.where(Material.user_id == int(scope))
    else:
        q = q.where(Material.user_id == user_id)
    if status:
        q = q.where(Material.status == status)
    if tag:
        # 按逗号分隔精确匹配，避免标签「服」误把「服装」的素材也筛出来
        norm = func.replace(Material.tags, "，", ",")
        q = q.where(("," + norm + ",").like(f"%,{tag},%"))
    if keyword:
        like = f"%{keyword}%"
        q = q.where((Material.title.like(like)) | (Material.description.like(like)))
    # 用 COUNT 而不是把所有行取回来数，素材上千条时列表页也不会慢
    total = session.exec(select(func.count()).select_from(Material)
                         .where(*[c for c in q._where_criteria])).one()
    q = q.order_by(Material.updated_at.desc()).offset((page - 1) * size).limit(size)
    return list(session.exec(q).all()), int(total)


def distinct_tags(session: Session, user_id: int, scope: str = "mine") -> list[str]:
    """该用户素材里出现过的分区标签（去重、排序），供素材库顶部标签筛选用。"""
    q = select(Material.tags)
    if scope == "all":
        pass
    elif scope.isdigit():
        q = q.where(Material.user_id == int(scope))
    else:
        q = q.where(Material.user_id == user_id)
    names: set[str] = set()
    for raw in session.exec(q).all():
        for t in str(raw or "").replace("，", ",").split(","):
            t = t.strip()
            if t:
                names.add(t)
    return sorted(names)


def purge(session: Session, material: Material) -> dict:
    """彻底删除：删素材记录 + 本地视频文件 + 缩略图/封面。

    文件若同时被其他素材记录引用（同一文件多人入库）则保留，避免误删别人的素材。
    素材还在发布队列里时拒绝删除，防止队列任务指向空素材。
    """
    from ..models import MaterialAccountConfig, MaterialTag, PublishTarget

    pending = session.exec(
        select(PublishTarget).where(
            PublishTarget.material_id == material.id,
            PublishTarget.status.in_(["queued", "wait_time", "running"]),
        )
    ).all()
    if pending:
        raise ValueError("该素材还在发布队列里，请先在「发布队列」把它移除，再彻底删除")

    removed = {"file": False, "thumb": False, "cover": False}
    # 同一路径被别的素材引用时不删物理文件
    shared = session.exec(
        select(Material).where(Material.file_path == material.file_path, Material.id != material.id)
    ).first()
    if not shared:
        p = Path(material.file_path)
        if p.is_file():
            try:
                p.unlink()
                removed["file"] = True
            except OSError:
                pass
    for key, attr in (("thumb", "thumbnail_path"), ("cover", "cover_path")):
        fp = getattr(material, attr, "") or ""
        if fp and Path(fp).is_file():
            try:
                Path(fp).unlink()
                removed[key] = True
            except OSError:
                pass

    for row in session.exec(select(MaterialAccountConfig).where(MaterialAccountConfig.material_id == material.id)).all():
        session.delete(row)
    for row in session.exec(select(MaterialTag).where(MaterialTag.material_id == material.id)).all():
        session.delete(row)
    session.delete(material)
    session.commit()
    return removed


def get_material(session: Session, material_id: int, user_id: int | None = None) -> Material | None:
    """按 ID 取素材。传入 user_id 则做归属校验（越权访问按不存在处理）。

    调度器等系统内部调用可不传 user_id；所有面向用户的 API 必须传。
    """
    m = session.get(Material, material_id)
    if m is None:
        return None
    if user_id is not None and m.user_id != user_id:
        return None
    return m


def update_material(session: Session, material: Material, **fields) -> Material:
    for k, v in fields.items():
        if hasattr(material, k):
            setattr(material, k, v)
    material.updated_at = now()
    session.add(material)
    session.commit()
    session.refresh(material)
    return material


def to_ready(session: Session, material: Material) -> Material:
    """人工确认后：inbox → ready。"""
    material.status = MaterialStatus.READY.value
    material.updated_at = now()
    session.add(material)
    session.commit()
    session.refresh(material)
    return material


def mark_published(session: Session, material: Material) -> None:
    material.status = MaterialStatus.PUBLISHED.value
    material.updated_at = now()
    session.add(material)
    session.commit()


def move_trash(session: Session, material: Material) -> Material:
    """ready/published → trash。仅改状态，不动磁盘文件。"""
    material.status = MaterialStatus.TRASH.value
    material.updated_at = now()
    session.add(material)
    session.commit()
    session.refresh(material)
    return material


def restore(session: Session, material: Material) -> Material:
    """trash → ready。与 to_ready/move_trash 保持同一返回约定（返回更新后的对象）。"""
    material.status = MaterialStatus.READY.value
    material.updated_at = now()
    session.add(material)
    session.commit()
    session.refresh(material)
    return material


def detect_orphans(session: Session, user_id: int) -> list[Material]:
    """找出文件已丢失的素材。"""
    out = []
    for m in session.exec(select(Material).where(Material.user_id == user_id)).all():
        if not Path(m.file_path).exists():
            out.append(m)
    return out


def generate_thumbnail(material: Material) -> str:
    """用 ffmpeg 抽首帧缩略图。返回缩略图路径。"""
    if not material.file_path or not Path(material.file_path).exists():
        return ""
    out = settings.thumbs_dir / f"{material.id}.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", material.file_path, "-ss", "1",
             "-vframes", "1", "-q:v", "5", str(out)],
            capture_output=True, timeout=30,
        )
        if out.exists():
            return str(out)
    except Exception:
        pass
    return ""
