"""视频首帧缩略图：把视频第一帧抽成小图存到 media/thumbs/。

## 为什么需要
卡片缩略图原先用 `<video preload="metadata">`，浏览器为了渲染首帧要对
**123MB 的视频文件**发 Range 请求读文件头。后果：
  - 每张卡一次网络往返，素材多时并发请求直接把浏览器打爆
  - 首帧常常在 moov atom 靠后（部分手机拍的视频 moov 在文件末尾），
    浏览器可能读了整个文件的前几 MB 才出画面
  - 页面观感差：等视频 metadata 时是一块黑

抽成一张几十 KB 的 jpg 后：单请求、无 Range、秒开。

## 关键取舍
- **抽帧失败不报错**，静默回退到 <video>（保持现有行为，绝不弄坏功能）
- 缓存 key 用 (文件大小, mtime) 而非仅路径 —— 文件被替换/改内容会自动重新抽
- 不覆盖用户自定义封面：只处理没有 cover_path 的素材
"""
import hashlib
import os
import subprocess
import time


THUMBS_DIRNAME = "thumbs"


def thumbs_dir() -> str:
    """缩略图目录：<项目根>/media/thumbs。

    不能放在 state/ 里——那里只放配置与记录，混进二进制图片
    会让备份包无谓变大（备份只打包 .json，但也容易被误当成状态文件）。
    """
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", ".."))
    d = os.path.join(root, "media", THUMBS_DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def thumb_name(video_path: str, width: int = 480, at_second: float = 0.5) -> str:
    """缓存文件名：取 (大小, mtime, 时间点, 宽度) 的 md5 前 16 位 + 宽度。

    为什么不用纯路径：同一个路径下文件被替换（重新剪了一版）时，
    纯路径会命中旧缓存，图就不对了。

    ⚠️ **必须把 at_second 也算进签名**：抽不同时间点是不同的图，
    若签名里不含时间，抽第 3 秒会命中第 0.5 秒的缓存 → 选帧永远跳不变。
    """
    try:
        st = os.stat(video_path)
        sig = f"{st.st_size}:{int(st.st_mtime)}:{width}:{at_second:.3f}"
    except OSError:
        sig = f"{video_path}:{width}:{at_second:.3f}"
    h = hashlib.md5(sig.encode("utf-8")).hexdigest()[:16]
    base = os.path.splitext(os.path.basename(video_path))[0]
    # 文件名里保留可读前缀，方便人工在目录里辨认
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)[:28]
    return f"{safe}_{h}.jpg"


def extract(video_path: str, at_second: float = 0.5, width: int = 480,
            timeout: int = 60) -> str:
    """抽第 at_second 秒的一帧存成 jpg。成功返回路径，失败返回 ""。

    at_second 默认 0.5 而不是 0：很多视频第 0 帧是纯黑/转场，0.5 秒更像真实画面。
    """
    if not video_path or not os.path.exists(video_path):
        return ""
    out = os.path.join(thumbs_dir(), thumb_name(video_path, width, at_second))
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out                      # 命中缓存

    exe = _ffmpeg_exe()
    if not exe:
        return ""
    # -ss 放在 -i 前：快速定位（先 seek 再解码），比放在后面快得多
    cmd = [
        exe, "-y", "-ss", str(at_second), "-i", video_path,
        "-frames:v", "1",
        "-vf", f"scale={width}:-2:flags=fast_bilinear",
        "-q:v", "5",                 # 质量5 ≈ jpg 体积/观感平衡点
        out,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        print(f"[WARN] 抽帧异常 {os.path.basename(video_path)}：{str(e)[:60]}")
        return ""
    if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        # 失败不留垃圾文件
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass
        return ""
    return out


def _ffmpeg_exe() -> str:
    """定位 ffmpeg。优先同目录，其次 PATH。"""
    here = os.path.dirname(os.path.abspath(__file__))
    # 兼容仓库根目录放 ffmpeg.exe 的情况
    for cand in (
        os.path.join(here, "ffmpeg.exe"),
        os.path.join(here, "..", "bin", "ffmpeg.exe"),
    ):
        c = os.path.abspath(cand)
        if os.path.exists(c):
            return c
    for name in ("ffmpeg", "ffmpeg.exe"):
        try:
            p = subprocess.run([name, "-version"], capture_output=True, timeout=8)
            if p.returncode == 0:
                return name
        except Exception:
            continue
    return ""


def extract_many(paths: list, workers: int = 2) -> dict:
    """批量抽帧。串行为主（ffmpeg 本身吃 CPU，并发收益有限且易抢磁盘）。

    返回 {video_path: thumb_path 或 ""}
    """
    out = {}
    for p in paths:
        out[p] = extract(p)
    return out


def prune(keep_days: int = 30, max_files: int = 500) -> dict:
    """清理过期缩略图，避免 media/thumbs 无限增长。"""
    d = thumbs_dir()
    removed = 0
    try:
        entries = []
        now = time.time()
        for fn in os.listdir(d):
            fp = os.path.join(d, fn)
            if not os.path.isfile(fp):
                continue
            try:
                age = now - os.path.getmtime(fp)
            except OSError:
                continue
            entries.append((fp, age))
        # 先删超龄的
        for fp, age in entries:
            if age > keep_days * 86400:
                try:
                    os.remove(fp)
                    removed += 1
                except OSError:
                    pass
        # 再按数量上限删最旧的
        rest = [(os.path.getmtime(f), f) for f in
                (os.path.join(d, x) for x in os.listdir(d))
                if os.path.isfile(os.path.join(d, x))]
        rest.sort()
        while len(rest) > max_files:
            _, fp = rest.pop(0)
            try:
                os.remove(fp)
                removed += 1
            except OSError:
                pass
    except OSError as e:
        print(f"[WARN] 缩略图清理异常：{str(e)[:60]}")
    return {"removed": removed}

# ===========================================================================
# 为「新建素材」表单提供的能力：预览帧 / 抓帧当封面 / 视频时长
# ===========================================================================
def probe_duration(video_path: str) -> float:
    """取视频时长（秒）。失败返回 0。

    为什么需要：抓帧当封面时，滑块的取值范围必须是真实时长，
    否则用户拖到 300 秒而视频只有 12 秒 → 抽出来是黑屏。
    """
    exe = _ffprobe_exe()
    if not exe or not video_path or not os.path.exists(video_path):
        return 0.0
    try:
        p = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json",
             "-show_format", video_path],
            capture_output=True, timeout=30,
        )
        if p.returncode != 0:
            return 0.0
        import json as _json
        info = _json.loads(p.stdout.decode("utf-8", "ignore") or "{}")
        return float((info.get("format") or {}).get("duration") or 0)
    except Exception:
        return 0.0


def _ffprobe_exe() -> str:
    """定位 ffprobe。优先与 ffmpeg 同目录，其次 PATH。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "ffprobe.exe"),
                 os.path.join(here, "..", "bin", "ffprobe.exe")):
        c = os.path.abspath(cand)
        if os.path.exists(c):
            return c
    ff = _ffmpeg_exe()
    if ff and ff != "ffmpeg":
        cand = ff.replace("ffmpeg", "ffprobe")
        if os.path.exists(cand):
            return cand
    for name in ("ffprobe", "ffprobe.exe"):
        try:
            if subprocess.run([name, "-version"], capture_output=True,
                              timeout=8).returncode == 0:
                return name
        except Exception:
            continue
    return ""


def preview_strip(video_path: str, count: int = 4) -> list:
    """抽 count 帧（均匀分布）供表单预览条用。返回 [路径,...]，失败返回 []。"""
    if not video_path or not os.path.exists(video_path):
        return []
    dur = probe_duration(video_path)
    # 时长未知时退化为按固定秒数抽样（短视频常见）
    points = ([1.0 + (i * 2.0) for i in range(count)] if dur <= 0
              else [dur * (i + 1) / (count + 1) for i in range(count)])
    out = []
    for t in points:
        t = max(0.1, min(t, (dur or 999) - 0.1))
        p = extract(video_path, at_second=t, width=320)
        if p:
            out.append(p)
    return out


def grab_cover(video_path: str, at_second: float = 1.0,
               width: int = 1080) -> str:
    """抽一帧存成正式封面（宽1080，符合视频号封面规格），返回路径。

    与 extract 的区别：宽度更大、命名带 cover 标记，
    且**存进 media/ 而不是 thumbs/**（缩略图会被定期清理，
    封面是素材数据的一部分，不能被清掉）。
    """
    if not video_path or not os.path.exists(video_path):
        return ""
    import json as _json

    try:
        st = os.stat(video_path)
        h = hashlib.md5(
            f"{st.st_size}:{int(st.st_mtime)}:{width}:{at_second:.2f}"
            .encode("utf-8")).hexdigest()[:12]
    except OSError:
        return ""
    base = os.path.splitext(os.path.basename(video_path))[0]
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)[:24]
    media = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "media"))
    os.makedirs(media, exist_ok=True)
    out = os.path.join(media, f"cover_{safe}_{h}.jpg")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out

    exe = _ffmpeg_exe()
    if not exe:
        return ""
    cmd = [exe, "-y", "-ss", f"{max(0.0, at_second):.2f}", "-i", video_path,
           "-frames:v", "1",
           "-vf", f"scale={width}:-2:flags=fast_bilinear",
           "-q:v", "2", out]          # q=2 质量更高（封面要清晰）
    try:
        if subprocess.run(cmd, capture_output=True, timeout=90).returncode != 0:
            return ""
    except Exception:
        return ""
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass
        return ""
    return out
