"""视频规格预检：发布前检查文件大小、分辨率、时长。"""
from pathlib import Path
import subprocess
import json


class VideoValidationError(Exception):
    pass


def validate_video(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise VideoValidationError(f"文件不存在：{path}")
    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > 4096:
        raise VideoValidationError(f"文件过大：{size_mb:.0f}MB，上限 4GB")

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(p)],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",  # Windows 默认 GBK，遇中文文件名会 UnicodeDecodeError
        )
        data = json.loads(r.stdout)
        v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        w = int(v.get("width", 0))
        h = int(v.get("height", 0))
        dur = float(data.get("format", {}).get("duration", 0))
        if w < 720 and h < 720:
            raise VideoValidationError(f"分辨率过低：{w}x{h}，建议 ≥720p")
        if dur > 7200:
            raise VideoValidationError(f"视频过长：{dur/60:.0f} 分钟，上限 2 小时")
        return {"size_mb": round(size_mb, 1), "width": w, "height": h, "duration_sec": dur}
    except VideoValidationError:
        raise
    except Exception as e:
        raise VideoValidationError(f"ffprobe 探测失败：{e}")


def validate_short_title(title: str) -> None:
    if len(title) < 7:
        raise VideoValidationError(f"短标题太短（{len(title)} 字），至少 7 字")
    if len(title) > 15:
        raise VideoValidationError(f"短标题太长（{len(title)} 字），最多 15 字")
