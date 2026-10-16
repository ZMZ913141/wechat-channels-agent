"""发布记录（batch / target）—— 数据看板的数据源。

## 为什么要有这个模块
原先发布记录散落两处且都有问题：
- `materials.json > publish_history`：只能在"素材复用到多号"时表达，
  会随素材删除/改名而丢历史，无法跨账号聚合。
- `state/publish_job.json`：**覆盖式单文件**，只保留最后一次任务，
  连历史都没有。

本模块按业界通行做法改为两层模型（借鉴 social-auto-upload 的发布记录思路、
MediaCrawler 的"记录与素材分离"原则）：

    batch  = 一次「点发布」动作
    target = batch × 一个账号  ← 统计的最小单位

## 三态而非布尔
**不要用 `ok: true/false`**——二态无法表达「已提交但未能确认」，统计口径会失真。
本模块用三态：
- `success`   已跳转到内容管理页（最强证据，平台侧审核结果不可知）
- `failed`    明确失败
- `unverified`已点击发表但未检测到成功信号（**不等于失败**，不应触发重试）
- `skipped`   被跳过（账号停用/素材不可用等，不进成功率分母）

## 成功口径（消除歧义）
视频号没有状态回查接口，「跳转到内容管理页」是能做到的最强证据，
但它只说明**已提交**，不代表**已过审**。因此每条记录都写明
`success_criterion`，UI 上也固定显示口径说明，避免用户误以为视频一定过审。

## append-only
重试 = 追加新 target（旧 target 的 `retry_of` 被新行指向），
**不修改旧行**。这样历史统计不会被重试污染。
"""
import json
import os
import time
import uuid

from . import config

# 成功判定口径锚点：写进每条记录，将来口径变了历史数据可区分
SUCCESS_CRITERION = "landed_on_manage_page"

# 错误码枚举：统计失败分布必须按错误码聚合，不能按自由文本
# （文案会变、会碎，按文本统计没有意义）
ERROR_CODES = {
    "ACCOUNT_BUSY": "账号被其它任务占用",
    "NOT_LOGGED_IN": "账号未登录",
    "MANUAL_OPERATION": "账号正被手动操作占用",
    "UPLOAD_TIMEOUT": "视频上传超时",
    "UPLOAD_FAILED": "视频文件注入失败",
    "CODEC_UNSUPPORTED": "视频格式不被支持",
    "FORM_FIELD_MISSING": "页面表单结构变化",
    "VERIFY_REQUIRED": "需要安全验证/扫码核验",
    "CONTENT_REJECTED": "内容被平台拒绝",
    "MATERIAL_MISSING": "素材不存在或已删除",
    "UNKNOWN": "未知原因",
}

# 失败发生的阶段（便于定位是哪一步挂的）
ERROR_STAGES = ["login", "upload", "fill_form", "submit", "confirm", "verify"]

_BATCH_FILE = "publish_batches.json"


def _path() -> str:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    return os.path.join(config.STATE_DIR, _BATCH_FILE)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_batches() -> list:
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_batches(items: list) -> None:
    """原子写：先写临时文件再replace，避免看板读到半个文件。"""
    p = _path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def new_batch(material: dict, account_ids: list, overrides: dict | None = None,
              trigger: str = "ui_button") -> dict:
    """创建一个 batch（含每个账号的 target 占位）。"""
    ov = overrides or {}
    batch = {
        "batch_id": "b_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4],
        "material_id": material.get("id", ""),
        "material_title": material.get("title", ""),
        "trigger": trigger,
        "requested_at": _now(),
        "requested_ts": time.time(),
        "scheduled_at": ov.get("scheduled_at") or "",
        # 配置快照：回答「当时发的标题/声明是什么」，便于回溯
        "config_snapshot": {
            "short_title": ov.get("short_title", "") or material.get("short_title", ""),
            "ai_declare": ov.get("ai_declare", "") or material.get("ai_declare", ""),
            "visibility": ov.get("visibility", "公开") or material.get("visibility", "公开"),
            "scheduled_at": ov.get("scheduled_at", "") or material.get("scheduled_at", ""),
            "cover_path": ov.get("cover_path", "") or material.get("cover_path", ""),
            "original": ov.get("original", material.get("original", True)),
        },
        "targets": [
            {
                "target_id": "t_" + uuid.uuid4().hex[:8],
                "account_id": aid,
                "account_name": "",      # 运行时补：改名不影响历史
                "status": "pending",     # pending|running|success|failed|unverified|skipped
                "success_criterion": SUCCESS_CRITERION,
                "queued_at": _now(),
                "started_at": "",
                "finished_at": "",
                "duration_ms": 0,
                "error_code": "",
                "error_message": "",
                "error_stage": "",
                "attempt": 1,
                "retry_of": "",
                "screenshot": "",
            }
            for aid in account_ids
        ],
        "status": "running",
        "finished_at": "",
    }
    items = load_batches()
    items.append(batch)
    save_batches(items)
    return batch


def _mutate(batch_id: str, fn) -> bool:
    """按 batch_id 找到并原地修改（唯一允许 UPDATE 的入口）。"""
    items = load_batches()
    for b in items:
        if b.get("batch_id") == batch_id:
            fn(b)
            save_batches(items)
            return True
    return False


def mark_target(batch_id: str, account_id: str, **fields) -> None:
    """更新某个 target 的执行结果。字段见模块文档。"""
    def fn(b):
        for t in b.get("targets", []):
            if t.get("account_id") == account_id:
                t.update(fields)
                break
    _mutate(batch_id, fn)


def start_target(batch_id: str, account_id: str, account_name: str) -> None:
    mark_target(
        batch_id, account_id,
        status="running",
        account_name=account_name,
        started_at=_now(),
        started_ts=time.time(),
    )


def finish_target(batch_id: str, account_id: str, status: str,
                  ok_text: str = "", error_code: str = "",
                  error_stage: str = "", screenshot: str = "",
                  duration_ms: int = 0) -> None:
    """结束一个 target。status 必须是三态之一。"""
    assert status in ("success", "failed", "unverified", "skipped"), status
    mark_target(
        batch_id, account_id,
        status=status,
        result_text=(ok_text or "")[:200],
        error_code=error_code or "",
        error_message=(ok_text or "")[:200],
        error_stage=error_stage or "",
        screenshot=screenshot or "",
        duration_ms=int(duration_ms or 0),
        finished_at=_now(),
    )


def close_batch(batch_id: str) -> None:
    """结算 batch 整体状态：全成功/部分成功/全失败。

    借鉴业界做法：一个账号失败不中断整批，最终给出三种整体结果。
    """
    def fn(b):
        ts = b.get("targets", [])
        done = [t for t in ts if t.get("status") in ("success", "failed", "unverified", "skipped")]
        succ = [t for t in done if t.get("status") == "success"]
        if not done:
            st = "running"
        elif len(succ) == len(done):
            st = "success"
        elif succ:
            st = "partial_success"
        else:
            st = "failed"
        b["status"] = st
        b["finished_at"] = _now()
    _mutate(batch_id, fn)


def classify_error(text: str) -> tuple:
    """把自由文本的失败原因映射为 (error_code, error_stage)。

    统计必须按 error_code 聚合，所以这里做一次归一化。
    """
    t = (text or "").lower()
    if "未登录" in t or "扫码" in t:
        return "NOT_LOGGED_IN", "login"
    if "手动操作" in t:
        return "MANUAL_OPERATION", "submit"
    if "占用" in t or "锁定" in t:
        return "ACCOUNT_BUSY", "submit"
    if "上传" in t and ("超时" in t or "未在" in t):
        return "UPLOAD_TIMEOUT", "upload"
    if "注入" in t or "上传入口" in t:
        return "UPLOAD_FAILED", "upload"
    if "安全验证" in t or "验证码" in t or "资质" in t or "实名" in t:
        return "VERIFY_REQUIRED", "verify"
    if "无法填写" in t or "结构" in t or "找不到" in t:
        return "FORM_FIELD_MISSING", "fill_form"
    if "格式" in t or "编码" in t or "不支持" in t:
        return "CODEC_UNSUPPORTED", "upload"
    if "素材" in t and "不存在" in t:
        return "MATERIAL_MISSING", "submit"
    return "UNKNOWN", ""


def iter_targets(days: int = 0, account_id: str = ""):
    """按时间倒序遍历所有 target（days=0 表示不限）。"""
    cutoff = ""
    if days:
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))
    for b in load_batches():
        if cutoff and b.get("requested_at", "") < cutoff:
            continue
        for t in b.get("targets", []):
            if account_id and t.get("account_id") != account_id:
                continue
            yield b, t


def prune(keep_days: int = 180) -> int:
    """清理过老的 batch（默认半年）。返回删除数。"""
    items = load_batches()
    cutoff = time.time() - keep_days * 86400
    keep = [b for b in items if float(b.get("requested_ts") or 0) >= cutoff]
    if len(keep) != len(items):
        save_batches(keep)
    return len(items) - len(keep)