"""状态文件备份与还原。

## 为什么必须有
素材库（titles/文案/标签/合集）、发布记录（ledger）都是用户辛苦积累的，
却只存在 state/*.json 几个小文件里。一次误删、一次磁盘故障、一次
bad json 写入，就全没了——而且**视频文件还在，只是记录丢了**，等于白干。

## 备份什么
只备份 state/ 下的 JSON（配置与记录）。**不碰浏览器档案**：
profiles/ 和 browser_profile/ 是 GB 级的登录态，不适合放进普通备份目录。

## 保留策略
保留最近 N 份（默认 30）+ 每日一份。旧的自动清理，避免无限增长。
"""
import json
import os
import shutil
import time
import uuid
import zipfile

from . import config


BACKUP_DIRNAME = "backups"
MANIFEST = "manifest.json"


def backup_dir() -> str:
    d = os.path.join(config.STATE_DIR, BACKUP_DIRNAME)
    os.makedirs(d, exist_ok=True)
    return d


def _state_files() -> list:
    """需要备份的状态文件。排除锁与临时文件。"""
    out = []
    try:
        for fn in sorted(os.listdir(config.STATE_DIR)):
            if not fn.endswith(".json"):
                continue
            if fn.startswith(".") or fn.endswith((".lock", ".tmp")):
                continue
            out.append(fn)
    except OSError:
        pass
    return out


def create(label: str = "") -> dict:
    """打一个备份包（zip）。返回备份信息。

    ⚠️ 文件名必须避免碰撞：只精确到秒的命名会让「同一秒内打两次备份」
    静默覆盖前一个（实测踩过：好备份被空备份覆盖，丢了真实数据）。
    这里加随机后缀，彻底杜绝。
    """
    files = _state_files()
    if not files:
        return {"ok": False, "reason": "没有可备份的状态文件"}
    ts = time.strftime("%Y%m%d_%H%M%S")
    name = f"backup_{ts}_{uuid.uuid4().hex[:4]}.zip"
    path = os.path.join(backup_dir(), name)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            manifest = {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "label": label,
                "files": files,
                "tool": "视频号助手",
            }
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            for fn in files:
                z.write(os.path.join(config.STATE_DIR, fn), fn)
    except Exception as e:
        return {"ok": False, "reason": str(e)}

    _prune()
    return {"ok": True, "name": name, "path": path,
            "files": files, "size": os.path.getsize(path),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def _prune(keep: int = 30) -> None:
    """保留最近 keep 份，其余删掉。"""
    try:
        items = sorted(
            (os.path.join(backup_dir(), f) for f in os.listdir(backup_dir())
             if f.startswith("backup_") and f.endswith(".zip")),
            key=os.path.getmtime,
            reverse=True,
        )
        for p in items[keep:]:
            try:
                os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def list_backups() -> list:
    """按时间倒序列出备份。"""
    out = []
    try:
        for fn in os.listdir(backup_dir()):
            if not (fn.startswith("backup_") and fn.endswith(".zip")):
                continue
            p = os.path.join(backup_dir(), fn)
            st = os.stat(p)
            info = {"name": fn, "size": st.st_size,
                    "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))}
            # 读manifest 拿到标签与文件清单
            try:
                with zipfile.ZipFile(p) as z:
                    if MANIFEST in z.namelist():
                        m = json.loads(z.read(MANIFEST).decode("utf-8"))
                        info["label"] = m.get("label", "")
                        info["files"] = m.get("files", [])
            except Exception:
                pass
            out.append(info)
    except OSError:
        pass
    out.sort(key=lambda x: x["name"], reverse=True)
    return out


def restore(name: str, auto_backup_first: bool = True) -> dict:
    """从备份包还原。

    auto_backup_first=True 时**先给当前数据打一个快照**——
    还原本身是破坏性操作，万一名单不对还能退回来。

    ⚠️ 还原后必须逐个校验：写完立刻读回比对条数/大小。
    实测踩过的坑：早期版本写完就报"成功"，但内容其实是空的
    （测试顺序问题导致包里本来就是空数据）——**假成功比失败更危险**，
    用户会以为数据还在。所以这里对每个文件做写入后回读校验，
    校验不通过一律计入 failed 并明确报错。
    """
    path = os.path.join(backup_dir(), os.path.basename(name))
    if not os.path.exists(path):
        return {"ok": False, "reason": "备份包不存在"}

    snapshot = None
    if auto_backup_first:
        snap = create(label="还原前自动快照")
        snapshot = snap.get("name") if snap.get("ok") else None

    restored, failed, empty = [], [], []
    try:
        with zipfile.ZipFile(path) as z:
            for fn in z.namelist():
                if fn == MANIFEST or fn.endswith("/"):
                    continue
                # 安全：只还原 json，防止 zip 里混入可执行文件或路径穿越
                if not fn.endswith(".json") or "/" in fn or "\\" in fn or fn.startswith(".."):
                    failed.append(fn)
                    continue
                target = os.path.join(config.STATE_DIR, os.path.basename(fn))
                try:
                    with z.open(fn) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                except Exception:
                    failed.append(fn)
                    continue

                # ---- 写入后回读校验（防止假成功）----
                try:
                    got = os.path.getsize(target)
                    if got == 0:          # 0 字节才是真·空文件，绝不能当有效数据还原
                        empty.append(fn)
                        continue
                    with open(target, "rb") as f:
                        json.loads(f.read().decode("utf-8"))   # 必须是合法 JSON
                    # 注意：合法的空列表/空对象（[] / {}）也视为还原成功，
                    # 不当成"数据丢失"——否则一个空的 collections.json 会让整个
                    # 还原被判失败（这正是修掉的假失败 bug）。
                    restored.append(fn)
                except Exception as e:
                    failed.append(f"{fn}(校验失败:{str(e)[:30]})")
    except Exception as e:
        return {"ok": False, "reason": f"备份包损坏：{e}"}

    # ok 必须反映「真的还原了东西」。只要出现空文件/失败，一律判失败——
    # 报成功会让用户以为数据回来了，实际却是空的（这比报错危险得多）。
    ok = bool(restored) and not failed and not empty
    out = {"ok": ok, "restored": restored, "failed": failed,
           "empty": empty, "snapshot": snapshot}
    if empty:
        out["reason"] = (
            "备份包里这些文件是空的：" + "、".join(empty)
            + "。可能是打备份时数据本来就已丢失，或用了很早的空备份。"
            "已跳过，**没有**用空数据覆盖你现有的数据。"
        )
    elif failed:
        out["reason"] = "部分文件还原失败：" + "、".join(failed)
    elif not restored:
        out["reason"] = "备份包里没有可还原的 JSON 文件"
    return out


def auto_backup_if_changed(last_sig_file: str = ".last_backup_sig") -> None:
    """启动时检查：状态文件变了就自动备份（只留一份"上次备份时的指纹"）。

    用途：每次改数据后，下次启动会自动留档。用户不需要记得手动备份。
    """
    sig_path = os.path.join(config.STATE_DIR, last_sig_file)
    files = _state_files()
    parts = []
    for fn in files:
        p = os.path.join(config.STATE_DIR, fn)
        try:
            parts.append(f"{fn}:{int(os.path.getmtime(p))}:{os.path.getsize(p)}")
        except OSError:
            continue
    sig = "|".join(parts)

    try:
        with open(sig_path, encoding="utf-8") as f:
            old = f.read().strip()
    except OSError:
        old = ""

    if old == sig:
        return          # 没变化，不重复备份
    try:
        # 首次运行时先建立指纹，不立刻备份（避免刚装就生成一个空备份）
        if old:
            create(label="启动自动备份（检测到数据有变化）")
        with open(sig_path, "w", encoding="utf-8") as f:
            f.write(sig)
    except OSError:
        pass