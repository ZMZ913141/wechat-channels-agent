"""同步发布：把一条素材（本地视频+文案）依次发布到多个视频号账号。

流程（每个账号串行执行，互不争用登录态）：
  1. 取该账号的浏览器档案（独立登录态），加**账号锁 + 档案锁**双重互斥
  2. 确定性 Playwright 操作视频号发表页：上传 → 填字段 → 发表
     （失败时按配置回退 browser-use Agent「看懂页面」）
  3. 记录每个账号的成功/失败结果与未生效字段
  4. 全部成功 → 素材标记「已发布」；有失败 → 保持原状态并提示补发

## 多账号为什么不会互相冲突
- **账号锁**（`state/task_<id>.lock`）：同一账号同一时刻只允许一个发布任务，
  避免对同一个视频号重复提交。
- **档案锁**（`state/profile_<hash>.lock`）：每个账号一个独立浏览器档案，
  一个 Chromium 档案同一时刻只能被一个进程打开。两个不同账号 = 两个不同档案
  = 天然不冲突，可以并行；同账号才会互斥。
"""
import asyncio
import json
import os
import threading
import time

from browser_use import Agent

from . import accounts, config, materials, proclock
from .browser import get_browser, get_llm

# 账号级任务锁等待时长（秒）。同账号并发发布时，后到者最多等这么久；
# 默认0 = 不等，直接明确报错让用户稍后重试（避免用户干等）。
TASK_LOCK_WAIT_S = float(config.PUBLISH_TASK_LOCK_WAIT_S)


def _mins_left(pause: dict) -> int:
    """暂停窗口还剩多少分钟（至少显示 1 分钟，避免显示 0 让人困惑）。"""
    try:
        left = max(0, int(float(pause.get("until", 0)) - time.time()))
        return max(1, left // 60)
    except Exception:
        return 1

PUBLISH_TASK = """打开 {url}（视频号助手），完成一次视频发布：
1. 进入「发表视频」入口（发布/上传视频按钮）。
2. 在文件选择框中选择本地视频文件，路径为：{video_path}
3. 描述/文案栏填写：{desc}
{short_title_line}
{cover_line}
{ai_line}
{original_line}
{visibility_line}
{schedule_line}
{ext_line}
{download_line}
{last_step}

结果要求（只输出以下几种之一）：
- 发布成功
- 已保存为草稿（原因）
- 已设置定时发表
- 需要扫码登录
- 需要安全验证
- 发布失败（原因）"""


def _build_task(mat: dict, overrides: dict | None = None) -> str:
    """根据素材 + 表单覆盖项，组装 Agent 发布任务（对齐官方发布页字段）。"""
    ov = overrides or {}

    def pick(field: str) -> str:
        v = (ov.get(field) or "").strip()
        return v if v else (mat.get(field) or "").strip()

    desc = pick("desc") or materials.copy_text(mat)
    tags = pick("tags")
    if tags:
        tag_str = " ".join(f"#{t.strip()}" for t in tags.split(",") if t.strip())
        desc = f"{desc}\n{tag_str}" if desc else tag_str

    short = pick("short_title")
    short_title_line = f"4. 短标题（页面有单独的短标题/简短描述栏，6-16字）填写：{short}" if short else ""

    cover = (ov.get("cover_path") or mat.get("cover_path") or "").strip()
    if cover and os.path.exists(cover):
        cover_line = f"5. 如果页面上有「上传封面/选择封面」入口，选择本地封面文件：{os.path.abspath(cover)}（也可以从视频截帧作为封面）。"
    else:
        cover_line = "5. 不设置封面（使用默认或从视频截帧）。"

    ai = (ov.get("ai_declare") if ov.get("ai_declare") is not None else mat.get("ai_declare")) or ""
    ai = str(ai).strip()
    if ai and ai != "非AI内容":
        ai_line = (
            f"6. 视频标注（重要，依法必须声明）：在发布页找到「视频标注/AI生成内容声明」相关选项，"
            f"勾选/选择「{ai}」。如果找不到该入口，在结果里说明「未找到AI标注入口」。"
        )
    else:
        ai_line = "6. 视频标注：该素材非 AI 生成内容，无需标注。"

    orig = ov.get("original", mat.get("original"))
    if str(orig).lower() in ("1", "true", "on", "yes"):
        original_line = (
            "7. 声明原创：找到「声明原创」开关打开，如弹出协议/分类选择，勾选同意并选择一个合适的原创分类。"
        )
    else:
        original_line = ""

    vis = (ov.get("visibility") or mat.get("visibility") or "公开").strip()
    visibility_line = (
        f"8. 可见范围：设置为「{vis}」。"
        + ("同时关闭「允许下载」。下载开关如果在高级设置里，保持默认即可。" if str(ov.get("allow_download", "")).lower() not in ("1", "true", "on", "yes") else "同时开启「允许下载」。")
        if vis != "公开"
        else "8. 可见范围保持默认「公开」。"
    )

    sched = (ov.get("scheduled_at") or mat.get("scheduled_at") or "").strip()
    if sched:
        sched = sched.replace("T", " ")
        schedule_line = f"9. 定时发表：把发表方式切换为「定时发表」，设定时间为：{sched}（24小时制，北京时间）。"
    else:
        schedule_line = ""

    ext = (ov.get("ext_link") or mat.get("ext_link") or "").strip()
    ext_line = f"10. 扩展链接：填入链接 {ext}。" if ext else ""

    allow_comment = str(ov.get("allow_comment", mat.get("allow_comment", "true"))).lower()
    download_line = "" if allow_comment in ("1", "true", "on", "yes") else "11. 关闭「允许评论」开关。"

    step = 11 + sum(1 for s in (download_line,) if s)
    last_step = f"{step}. 确认信息无误后点击「发表」按钮提交（如果无法直接发表，选择保存草稿并在结果里说明）。"

    return PUBLISH_TASK.format(
        url=config.PUBLISH_URL,
        video_path=os.path.abspath(mat["video_path"]),
        desc=desc,
        short_title_line=short_title_line,
        cover_line=cover_line,
        ai_line=ai_line,
        original_line=original_line,
        visibility_line=visibility_line,
        schedule_line=schedule_line,
        ext_line=ext_line,
        download_line=download_line,
        last_step=last_step,
    )

JOB_FILE = os.path.join(config.STATE_DIR, "publish_job.json")
_job_lock = threading.Lock()


def _read_job() -> dict | None:
    try:
        with open(JOB_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_job(job: dict) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(JOB_FILE, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)


def start_publish_job(material_id: str, account_ids: list, overrides: dict | None = None) -> dict:
    """启动一个发布任务（后台线程串行执行）。返回任务初始状态。

    overrides：发布页表单覆盖项（短标题/描述/标签/AI声明/原创/可见范围/定时/扩展链接），
    留空的字段回退到素材自身配置。
    """
    mat = materials.get_material(material_id)
    if not mat:
        raise ValueError("素材不存在")
    vp = (mat.get("video_path") or "").strip()
    if not vp or not os.path.exists(vp):
        raise ValueError(f"视频文件不存在：{vp or '（未填路径）'}")
    if not account_ids:
        raise ValueError("未选择任何发布账号")

    accs = [accounts.get_account(a) for a in account_ids]
    accs = [a for a in accs if a]
    if not accs:
        raise ValueError("所选账号均无效")

    ov = overrides or {}
    ai = (ov.get("ai_declare") if ov.get("ai_declare") is not None else mat.get("ai_declare")) or ""
    job = {
        "material_id": material_id,
        "material_title": mat.get("title", ""),
        # 发布配置快照（通知与结果页展示用）
        "config": {
            "short_title": ov.get("short_title", ""),
            "ai_declare": str(ai).strip(),
            "visibility": ov.get("visibility") or mat.get("visibility") or "公开",
            "scheduled_at": ov.get("scheduled_at") or mat.get("scheduled_at") or "",
            "cover_path": ov.get("cover_path") or mat.get("cover_path") or "",
        },
        "accounts": [{"id": a["id"], "name": a["name"], "status": "排队中", "result": ""} for a in accs],
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "running": True,
    }
    with _job_lock:
        _write_job(job)

    t = threading.Thread(target=_run_job, args=(mat, accs, ov), daemon=True)
    t.start()
    return job


def read_job() -> dict | None:
    with _job_lock:
        return _read_job()


def _run_job(mat: dict, accs: list, overrides: dict | None = None) -> None:
    results = []
    for acc in accs:
        _update_account_status(acc["id"], "发布中…", "")
        try:
            ok, text = publish_one_account(acc, mat, overrides=overrides)
            status = "✅ 成功" if ok else "❌ 失败"
            results.append((acc, ok, text))
            _update_account_status(acc["id"], status, text)
        except Exception as e:
            results.append((acc, False, str(e)))
            _update_account_status(acc["id"], "❌ 失败", str(e))
        # 每号结束都写入素材发布历史（状态记录可追溯）
        ok_last = results[-1][1]
        materials.record_publish(mat["id"], acc, ok_last, results[-1][2])

    # 汇总通知 + 更新素材状态
    ok_count = sum(1 for _, ok, _ in results if ok)
    job = _read_job() or {}
    cfg = job.get("config", {})
    extra = []
    if cfg.get("ai_declare"):
        extra.append(f"AI标注：{cfg['ai_declare']}")
    if cfg.get("scheduled_at"):
        extra.append(f"定时：{cfg['scheduled_at']}")
    if cfg.get("visibility") and cfg["visibility"] != "公开":
        extra.append(f"可见：{cfg['visibility']}")
    extra_s = ("（" + "，".join(extra) + "）") if extra else ""
    lines = [f"素材「{mat.get('title','')}」同步发布完成：{ok_count}/{len(results)} 个账号成功{extra_s}"]
    for acc, ok, text in results:
        lines.append(f"- {acc['name']}: {'成功' if ok else '失败（' + text[:80] + '）'}")
    summary = "\n".join(lines)
    # 结果同时用于面板「发布中心」展示与落盘存档
    print(f"[INFO] 同步发布汇总：\n{summary}")

    if ok_count == len(results) and results:
        materials.set_status(mat["id"], "已发布")

    with _job_lock:
        job = _read_job() or {}
        job["running"] = False
        job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        job["summary"] = summary
        _write_job(job)


def _update_account_status(aid: str, status: str, result: str) -> None:
    with _job_lock:
        job = _read_job() or {}
        for a in job.get("accounts", []):
            if a["id"] == aid:
                a["status"] = status
                a["result"] = result
        _write_job(job)


def publish_one_account(acc: dict, mat: dict, overrides: dict | None = None) -> tuple[bool, str]:
    """发布单账号：先走「确定性填写」（无需 LLM），失败自动回退 browser-use Agent。

    两层互斥保证多账号安全：
      - 账号锁：同一账号不允许两个发布任务并发（防重复提交到同一个视频号）
      - 档案锁（在 publish_pw 内）：同一浏览器档案不允许两个进程同时打开
    不同账号之间**不互斥**，可以并行发布。
    """
    acc_id = acc["id"]

    # 手动操作暂停窗口：用户正在扫码登录/手动打开发表页时，这个账号的档案
    # 正被别人占用——此时发布只会得到「档案被占用」这种晦涩错误。
    # 这里提前判断并说清楚原因。
    pause = proclock.paused(acc_id)
    if pause:
        return False, (
            f"账号「{acc.get('name') or acc_id}」正在手动操作中（{pause.get('reason', '被打开发表页')}），"
            f"档案被占用，请约{_mins_left(pause)} 分钟后再发布"
        )

    got, holder = proclock.acquire_account(
        acc_id, tag=f"发布（{acc.get('name') or acc_id}）", wait_s=TASK_LOCK_WAIT_S
    )
    if not got:
        return False, (
            f"账号「{acc.get('name') or acc_id}」正被 {proclock.holder_desc(holder)} 占用，"
            "请等上一个任务结束（或换个账号）后重试"
        )
    try:
        if config.PUBLISH_METHOD == "deterministic":
            try:
                from .publish_pw import PublishFlowError, publish_deterministic

                ok, text = publish_deterministic(acc, mat, overrides)
                print(f"[INFO] 确定性发布：账号「{acc['name']}」→ {text[:80]}")
                return ok, text
            except PublishFlowError as e:
                print(f"[WARN] 确定性流程无法完成（{e}），回退 Agent 方案…")
            except Exception as e:
                print(f"[WARN] 确定性发布异常（{e}），回退 Agent 方案…")
            if not config.PUBLISH_AUTO_FALLBACK:
                return False, "确定性发布未成功，且已关闭自动回退（.env PUBLISH_AUTO_FALLBACK=false）"
        return asyncio.run(publish_to_account(acc, mat, overrides=overrides))
    finally:
        proclock.release_account(acc_id)


async def publish_to_account(acc: dict, mat: dict, max_retries: int = 1, overrides: dict | None = None) -> tuple[bool, str]:
    """browser-use Agent 发布单账号（回退方案）。返回 (是否成功, 结果文本)。"""

    task = _build_task(mat, overrides)

    browser = get_browser(acc, headless=config.PUBLISH_HEADLESS)
    agent = Agent(task=task, llm=get_llm(), browser=browser, use_vision=config.USE_VISION)
    result = None
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            result = await agent.run()
            last_err = None
            break
        except Exception as e:
            last_err = e
            print(f"[WARN] 账号「{acc['name']}」发布 agent 第 {attempt + 1} 次运行失败: {e}")
    try:
        await browser.close()
    except Exception:
        pass

    if result is None:
        return False, f"agent 运行异常：{last_err}"

    text = result.final_result() if hasattr(result, "final_result") else str(result)
    text = (text or "").strip()

    # ⚠️ 「已设置定时发表」不等于已发布：定时任务此刻可能只是排队中。
    # 但它确实代表「提交成功」，所以算成功、但要在文案里说清楚状态，
    # 否则用户以为视频已经发出去了。
    if "发布成功" in text:
        return True, text[:200]
    if "已设置定时发表" in text:
        when = (overrides or {}).get("scheduled_at") or (mat or {}).get("scheduled_at") or ""
        return True, f"已提交并设置定时发表（{when}），到点自动发布｜{text[:120]}"
    if "已保存为草稿" in text:
        return False, text[:200]
    if "需要扫码登录" in text:
        return False, "该账号未登录，请先在面板「账号管理」扫码登录"
    if "需要安全验证" in text:
        return False, "出现安全验证/验证码，请人工处理后重试"
    return False, text[:200] or "未知结果"
