"""视频文件探针：用 ffprobe 取分辨率/时长/大小，做发布前合规预检。

合规阈值（视频号官方）：分辨率 ≥ 720p、单个文件 ≤ 4GB、时长 ≤ 2 小时。
（不同来源有“≤1GB/≤2GB/≤1小时”的旧口径，以官方助手上限为准：4GB / 2 小时 / 720p。）
"""
import json
import os
import subprocess

LIMITS = {
    "max_gb": 4,
    "max_hours": 2,
    "min_height": 720,
}


def probe(path: str) -> dict | None:
    """返回 {width,height,duration,sample_rate?,size_mb} 或 None（不可用/失败）。"""
    ap = os.path.abspath(path)
    if not os.path.isfile(ap):
        return None
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", ap,
            ],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(out.stdout or "{}")
    except Exception:
        return None
    w = h = dur = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and w is None:
            w = s.get("width")
            h = s.get("height")
            dur = _to_float(s.get("duration"))
        if dur is None:
            dur = _to_float(s.get("duration"))
    if w is None and h is None:
        # 退而从 format 取时长
        dur = _to_float((info.get("format") or {}).get("duration"))
    size_mb = round(os.path.getsize(ap) / 1048576, 2)
    return {
        "width": w, "height": h,
        "duration": round(dur, 1) if dur else None,
        "size_mb": size_mb,
    }


def _to_float(v):
    try:
        return float(v)
    except Exception:
        return None


def check_compliance(info: dict | None) -> list[str]:
    """返回违规提示列表（空=合规）。"""
    if not info:
        return []
    warns = []
    # 读不到分辨率/文件为空：多半是空文件或损坏的伪视频，不能误报“合规”
    if not info.get("height") or not info.get("size_mb"):
        warns.append("无法读取视频信息（文件可能为空或已损坏），请先用播放器确认能否正常播放")
        return warns
    if info.get("size_mb") and info["size_mb"] > LIMITS["max_gb"] * 1024:
        warns.append(f"文件过大（{info['size_mb']}MB > {LIMITS['max_gb']}GB），平台可能拒绝")
    if info.get("duration") and info["duration"] > LIMITS["max_hours"] * 3600:
        warns.append(f"时长过长（{info['duration']/60:.0f}分钟 > {LIMITS['max_hours']}小时）")
    if info.get("height") and info["height"] < LIMITS["min_height"]:
        warns.append(f"分辨率偏低（{info['height']}p < {LIMITS['min_height']}p），影响画质/推荐")
    return warns
