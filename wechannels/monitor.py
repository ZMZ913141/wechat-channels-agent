"""私信+评论监控核心逻辑（支持多账号）。

用 browser-use 的 AI Agent 打开指定视频号助手，让模型把「私信会话」和
「评论管理」整理成 JSON，再与本地已读记录对比，发现新私信/新评论就触发通知；
若私信命中「固定回复规则」，还会自动发送预先配置的回复（见 reply.py）。

采用 browser-use 而非写死选择器：视频号网页改版时，只要页面“逻辑”大体
不变，模型通常仍能读懂并提取，维护成本远低于 Playwright 脚本。

针对“抗改版 + 稳定”做的加固：
  - 解析：兼容模型返回的 ```json 围栏 / 多余说明文字 / 数组或对象 / 单引号风格
  - 提取：任务要求先滚动加载全部会话，避免漏抓虚拟滚动内容
  - 去重：对消息文本做归一化，防止模型每轮返回略有差异导致重复通知
  - 锁：用 state/monitor_<aid>.lock 防止监控与发布/手动操作争用同一浏览器档案
        锁文件记录持有者 PID，**持有进程已死则立即接管**（否则强杀进程后
        残留锁会让后续每一轮都被静默跳过，最多 15 分钟什么都干不了）
"""
import asyncio
import ctypes
import json
import os
import re
import time

from browser_use import Agent

from . import accounts, config, proclock, reply
from .browser import get_browser, get_llm
from .notify import notify

DM_PART = """【第一部分：私信】
进入「私信」页面。如果会话列表较长，请先向下滚动几次，确保所有会话都已加载。
对页面上出现的每一个会话，记录三项信息：
- sender：对方昵称
- last_message：该会话最后一条消息的文字内容
- unread：是否为未读（true/false）"""

COMMENT_PART = """
【第二部分：评论】
进入「评论管理」页面（通常在内容管理/互动管理菜单下），查看最近的视频评论，
对每条评论记录：
- video：所属视频标题
- author：评论人昵称
- content：评论内容"""

OUTPUT_COMBINED = """
最后只输出一个 JSON 对象，不要任何额外解释文字，格式如下：
{{"dms":[{{"sender":"昵称A","last_message":"你好在吗","unread":true}}],"comments":[{{"video":"视频标题","author":"昵称B","content":"评论内容"}}]}}
某部分没有内容就输出空数组 []。"""

OUTPUT_DM_ONLY = """
只输出一个 JSON 数组，不要任何额外解释文字，格式如下：
[{{"sender":"昵称A","last_message":"你好在吗","unread":true}},{{"sender":"昵称B","last_message":"收到","unread":false}}]"""

COMMON_RULES = """
重要：
- 如果页面出现二维码、要求扫码登录或显示“已退出登录”，请直接回复：需要扫码登录。
- 如果出现滑块验证码、安全验证、人机校验等挑战页面，请直接回复：需要安全验证。
- 如果页面空白或明显加载失败，请直接回复：页面加载失败。"""

MONITOR_TASK = "打开 {url} 视频号助手，依次完成下面两部分：" + DM_PART + COMMENT_PART + OUTPUT_COMBINED + COMMON_RULES
MONITOR_TASK_DM_ONLY = "打开 {url} 视频号助手。" + DM_PART + OUTPUT_DM_ONLY + COMMON_RULES


# ---------------------------------------------------------------------------
# JSON 解析（抗模型输出抖动）
# ---------------------------------------------------------------------------
def _loads_any(text: str):
    """从模型返回文本中尽力提取 JSON（数组或对象）。失败返回 None。"""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip().strip("`").strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    for pattern in (r"\[.*\]", r"\{.*\}"):
        m = re.search(pattern, cleaned, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass

    # 兜底：模型偶尔返回单引号 / Python dict 风格
    candidate = cleaned
    m = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if m:
        candidate = m.group(0)
    else:
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            candidate = m.group(0)
    try:
        import ast

        return ast.literal_eval(candidate)
    except Exception:
        pass
    return None


def parse_conversations(text: str):
    """兼容旧格式：提取私信数组。无法解析返回 None。"""
    data = _loads_any(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "dms" in data:
            return data.get("dms") or []
        return [data]
    return None


def parse_monitor_result(text: str) -> tuple[list | None, list | None]:
    """解析监控结果。返回 (dms, comments)；comments 仅在组合任务下有值。"""
    data = _loads_any(text)
    if isinstance(data, dict) and ("dms" in data or "comments" in data):
        dms = data.get("dms")
        comments = data.get("comments")
        return (dms if isinstance(dms, list) else None), (comments if isinstance(comments, list) else [])
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        return [data], None
    return None, None


def _normalize(text: str) -> str:
    """归一化消息文本，用于去重比对。"""
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"\s+", " ", t)
    t = t.rstrip("。.…~… ")
    return t


def load_seen(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_seen(seen: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def _replied_key(scope: str, who: str) -> str:
    """「已自动回复」的去重键。

    私信按会话人去重；评论按评论人去重（不看视频）——同一个人跨多个视频评论
    只回一次，避免把人家刷烦。
    """
    return f"replied:{scope}:{who}"


def _can_auto_reply(seen: dict, rkey: str) -> bool:
    """判断现在能否对某人自动回复，受 once / cooldown 控制。"""
    rec = seen.get(rkey)
    if not rec:
        return True
    if config.AUTO_REPLY_ONCE_PER_USER:
        return False
    if config.AUTO_REPLY_COOLDOWN > 0:
        last = float(rec.get("replied_at") or 0)
        if time.time() - last < config.AUTO_REPLY_COOLDOWN:
            return False
    return True


def pid_alive(pid: int) -> bool:
    """判断某个进程是否还活着。

    注意：**Windows 上不能用 os.kill(pid, 0)** —— CPython 在 Windows 下把
    非 CTRL_* 信号一律实现为 TerminateProcess，`os.kill(pid, 0)` 会真的把
    那个进程杀掉（退出码 0）。所以这里走 ctypes 的 OpenProcess/GetExitCodeProcess。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k = ctypes.windll.kernel32
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if k.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True


# 锁超过这个时长即使持有进程还活着也强制接管（防止真正卡死的进程长期占锁）
LOCK_STALE_SECONDS = 900


def _acquire_lock(lock_path: str) -> bool:
    """简易文件锁：同一账号的浏览器档案同时只允许一个任务使用。

    残留锁处理：锁文件里写了持有者 PID。**只有持有进程还活着**才认为锁有效，
    否则（进程被强杀 / 崩溃）立即接管——不然监控每一轮都会被静默跳过。
    """
    try:
        if os.path.exists(lock_path):
            holder = 0
            try:
                with open(lock_path, encoding="utf-8") as f:
                    holder = int((f.read() or "0").strip() or 0)
            except Exception:
                holder = 0
            age = time.time() - os.path.getmtime(lock_path)
            if pid_alive(holder):
                if age < LOCK_STALE_SECONDS:
                    return False
                print(
                    f"[WARN] 锁 {os.path.basename(lock_path)} 已存在 {age:.0f}s，"
                    f"持有进程 {holder} 仍存活，强制接管"
                )
            else:
                print(
                    f"[INFO] 清理残留锁 {os.path.basename(lock_path)}"
                    f"（持有进程 {holder} 已不存在）"
                )
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        print(f"[WARN] 获取锁失败 {lock_path}: {e}")
        return False


def _release_lock(lock_path: str) -> None:
    """释放锁。只删自己持有的锁，避免误删被其它进程接管的锁。"""
    try:
        if os.path.exists(lock_path):
            holder = 0
            try:
                with open(lock_path, encoding="utf-8") as f:
                    holder = int((f.read() or "0").strip() or 0)
            except Exception:
                holder = 0
            if holder and holder != os.getpid():
                return
        os.remove(lock_path)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def clear_stale_locks() -> list:
    """清理所有「持有进程已死」的残留锁，返回被清理的文件名列表。

    覆盖两种锁：
      1) 老的 `monitor_*.lock`（内容是纯 PID 文本）；
      2) 新的 `profile_*.lock`（proclock 的档案锁，内容是 JSON，含 pid）。
    第二类以前没被清理到——被强杀后 lock 会一直留着，面板按钮点了也“没反应”。
    """
    import json as _json

    cleaned = []
    try:
        names = os.listdir(config.STATE_DIR)
    except Exception:
        names = []
    for name in names:
        is_old = name.startswith("monitor_") and name.endswith(".lock")
        is_profile = name.startswith("profile_") and name.endswith(".lock")
        if not (is_old or is_profile):
            continue
        p = os.path.join(config.STATE_DIR, name)
        holder = 0
        try:
            with open(p, encoding="utf-8") as f:
                raw = (f.read() or "").strip()
        except Exception:
            raw = ""
        try:
            if is_profile:
                holder = int((_json.loads(raw) or {}).get("pid") or 0)
            else:
                holder = int(raw or 0)
        except Exception:
            holder = 0
        if not pid_alive(holder):
            try:
                os.remove(p)
                cleaned.append(name)
            except Exception:
                pass
    return cleaned


# ---------------------------------------------------------------------------
# 心跳：供面板显示“是否还活着”与看门狗判断
# ---------------------------------------------------------------------------
def read_heartbeat() -> dict:
    try:
        with open(config.HEARTBEAT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_heartbeat(data: dict) -> None:
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(config.HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def touch_heartbeat(account: str = "") -> None:
    hb = read_heartbeat()
    hb["ts"] = time.time()
    if account:
        hb["account"] = account
    hb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_heartbeat(hb)


def mark_success(account: str) -> None:
    hb = read_heartbeat()
    hb["ts"] = time.time()
    hb["last_success"] = time.time()
    hb["account"] = account
    hb["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_heartbeat(hb)


# ---------------------------------------------------------------------------
# 确定性提取（Playwright 取文本 + 模型做文本→JSON）
# ---------------------------------------------------------------------------
try:  # playwright 不可用时不影响其它功能
    from .monitor_pw import NeedLogin, ProfileBusy, grab as _pw_grab
except Exception:  # pragma: no cover
    class NeedLogin(Exception):  # type: ignore[no-redef]
        pass

    class ProfileBusy(Exception):  # type: ignore[no-redef]
        pass

    _pw_grab = None


_LOGIN_WARN_GAP = 3600  # 同一条「未登录」告警 1 小时内只推一次


def _should_notify(aid: str, msg: str) -> bool:
    """抑制重复告警：未登录的账号每轮都会报，不抑制会一直刷屏。"""
    if "未登录" not in msg:
        return True
    path = os.path.join(config.STATE_DIR, f"warn_{aid}.json")
    now = time.time()
    try:
        d = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    except Exception:
        d = {}
    if now - float(d.get("ts", 0)) < _LOGIN_WARN_GAP:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": now}, f)
    except Exception:
        pass
    return True


async def _llm_complete(prompt: str) -> str:
    """直接调用模型做纯文本补全（不操作浏览器）。"""
    llm = get_llm()
    try:
        from browser_use.llm.messages import UserMessage

        r = await llm.ainvoke([UserMessage(content=prompt)])
    except Exception:
        r = await llm.ainvoke(prompt)
    return (getattr(r, "completion", None) or getattr(r, "content", None) or str(r)).strip()


async def _deterministic_fetch(acc: dict, want_comment: bool = True):
    """确定性提取一轮：返回 (dms, comments)；未登录抛 NeedLogin。

    思路：Playwright 穿透无界 iframe 拿到纯文本（确定性、几秒完成），
    再让模型把纯文本整理成 JSON（纯文本任务，模型很稳）。
    避免让模型去操作浏览器——它读不进 wujie iframe，会空转到超时。
    """
    if _pw_grab is None:
        raise RuntimeError("Playwright 提取不可用")
    texts = await asyncio.to_thread(_pw_grab, acc, want_comment)
    if not (texts.get("dm") or texts.get("comment")):
        raise RuntimeError("未读取到私信/评论内容（页面可能改版或未渲染）")

    schema = (
        '{"dms":[{"sender":"昵称","last_message":"最后一条消息","unread":true}],'
        '"comments":[{"video":"视频标题","author":"评论人","content":"评论内容"}]}'
    )
    detail = texts.get("comment_detail") or ""
    prompt = (
        "下面是视频号助手「私信」与「评论管理」页面的纯文本抓取结果。\n"
        "请只依据这些内容提取信息，只输出一个 JSON 对象，不要任何解释文字，格式：\n"
        f"{schema}\n"
        "规则：没有内容就输出空数组 []；抓不到就留空，**绝对不要编造**。\n"
        "「评论明细」是从视频里展开出来的真实评论，优先级最高，务必逐条提取。\n\n"
        f"【私信页面文本】\n{(texts.get('dm') or '')[:4000]}\n\n"
        f"【评论明细（最重要）】\n{detail[:8000]}\n\n"
        f"【评论管理-视频列表（含评论数）】\n{(texts.get('comment') or '')[:2500]}"
    )
    raw = await _llm_complete(prompt)
    dms, comments = parse_monitor_result(raw)
    if dms is None:
        raise RuntimeError(f"模型未能解析为 JSON：{raw[:200]}")
    return dms or [], comments or []


# ---------------------------------------------------------------------------
# 一轮监控
# ---------------------------------------------------------------------------
async def run_once(account: dict | None = None, max_retries: int = 2) -> None:
    """执行一轮监控：私信(+评论)提取 -> 去重 -> 通知 -> 规则自动回复。

    account：要监控的账号（None 用当前账号）。
    """
    acc = account or accounts.current_account()
    seen_path = accounts.seen_path(acc)
    lock_path = os.path.join(config.STATE_DIR, f"monitor_{acc['id']}.lock")

    # 「手动操作」暂停窗口内跳过本账号：把浏览器档案让给「打开发表页 / 扫码登录」，
    # 否则两边同时开同一个档案，手动那边会拿不到登录态（显示二维码）。
    pz = proclock.paused(acc["id"])
    if pz:
        print(f"[INFO] 账号「{acc['name']}」{proclock.paused_desc(acc['id'])}，本轮跳过")
        touch_heartbeat(acc["id"])
        return

    if not _acquire_lock(lock_path):
        print(f"[WARN] 账号「{acc['name']}」监控锁被占用（可能正在发布/另一轮监控），跳过本轮")
        return

    # ---- 第一步：提取 ----
    # 优先走「确定性提取」（Playwright 穿透无界 iframe 取文本 + 模型只做文本→JSON）；
    # 视频号助手是 wujie 微前端，browser-use 的 Agent 读不进 iframe，会空转超时。
    browser = None
    dms: list | None = None
    comments: list | None = None
    early_exit: str | None = None
    try:
        dms, comments = await _deterministic_fetch(acc, config.MONITOR_COMMENTS)
        print(f"[INFO] 账号「{acc['name']}」确定性提取完成（私信 {len(dms or [])} / 评论 {len(comments or [])}）")
    except ProfileBusy as e:
        # 档案被手动操作占着：这不是故障，不当告警推送，只写日志
        print(f"[INFO] {e}")
        _release_lock(lock_path)
        return
    except NeedLogin:
        early_exit = f"账号「{acc['name']}」未登录，请用面板「账号管理 → 扫码登录」登录后再启动监控"
    except Exception as e:
        print(f"[WARN] 账号「{acc['name']}」确定性提取失败，回退 AI Agent：{e}")

    if early_exit is None and dms is None:
        # ---- 回退：AI Agent（页面结构变化 / Playwright 不可用时兜底）----
        task = MONITOR_TASK if config.MONITOR_COMMENTS else MONITOR_TASK_DM_ONLY
        browser = get_browser(acc)
        agent = Agent(
            task=task.format(url=config.CHANNELS_URL),
            llm=get_llm(),
            browser=browser,
            use_vision=config.USE_VISION,
        )
        result = None
        last_err = None
        for attempt in range(max_retries + 1):
            try:
                result = await agent.run()
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"[WARN] 账号「{acc['name']}」agent 第 {attempt + 1} 次运行失败: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(min(30, 5 * (attempt + 1)))

        if result is None:
            try:
                await browser.close()
            except Exception:
                pass
            _release_lock(lock_path)
            notify(
                "视频号监控",
                f"账号「{acc['name']}」连续 {max_retries + 1} 次提取失败（可能断网/接口异常）：{last_err}",
                **_notify_kwargs(acc),
            )
            return

        text = result.final_result() if hasattr(result, "final_result") else str(result)

        # 异常页面：直接通知并结束本轮（不硬刚反爬）
        if "需要扫码登录" in text:
            early_exit = f"账号「{acc['name']}」未登录，请用面板「账号管理 → 扫码登录」登录后再启动监控"
        elif "需要安全验证" in text:
            early_exit = f"账号「{acc['name']}」出现安全验证/验证码，请人工处理"
        elif "页面加载失败" in text:
            early_exit = f"账号「{acc['name']}」页面加载失败，可能网络异常，稍后重试"

        if not early_exit:
            dms, comments = parse_monitor_result(text)
            if dms is None:
                early_exit = f"账号「{acc['name']}」未能解析监控结果，模型原始返回：{text[:500]}"

    if early_exit:
        try:
            if browser is not None:
                await browser.close()
        except Exception:
            pass
        _release_lock(lock_path)
        if _should_notify(acc["id"], early_exit):
            notify("视频号监控", early_exit, **_notify_kwargs(acc))
        return

    # 走到这里说明本轮正常（视为一次成功检查）
    mark_success(acc["id"])
    seen = load_seen(seen_path)

    # ---- 私信：去重 + 通知 +（可选）固定规则自动回复 ----
    pending_replies: list[tuple[str, str]] = []  # (sender, reply)
    pending_comment_replies: list[tuple[str, str, str, str]] = []  # (video, author, content, reply)
    new_dms = 0
    for c in dms:
        key = c.get("sender")
        if not key:
            continue
        last = c.get("last_message", "")
        unread = c.get("unread", False)
        prev = seen.get(f"dm:{key}")
        prev_norm = _normalize(prev.get("last_message", "")) if prev else None
        if prev is None or prev_norm != _normalize(last):
            if unread or prev is None:
                notify(
                    "视频号新私信",
                    f"账号「{acc['name']}」 来自「{key}」：{last}",
                    **_notify_kwargs(acc),
                )
                new_dms += 1
                hit = reply.match_reply(last, scope="dm")
                if hit and config.AUTO_REPLY_ENABLED:
                    rkey = _replied_key("dm", key)
                    if _can_auto_reply(seen, rkey):
                        pending_replies.append((key, hit))
                    else:
                        print(f"[INFO] 私信「{key}」已自动回复过（或在冷却中），本轮不再重复回复")
        seen[f"dm:{key}"] = {"last_message": last, "unread": unread}

    # ---- 评论：去重 + 通知 +（可选）固定规则自动回复 ----
    new_comments = 0
    for c in comments or []:
        author = c.get("author")
        content = c.get("content", "")
        video = c.get("video", "")
        if not author or not content:
            continue
        key = f"comment:{author}:{video}"
        prev = seen.get(key)
        if prev is None or _normalize(prev.get("content", "")) != _normalize(content):
            notify(
                "视频号新评论",
                f"账号「{acc['name']}」 「{video}」下 {author} 评论：{content}",
                **_notify_kwargs(acc),
            )
            new_comments += 1
            chit = reply.match_reply(content, scope="comment")
            if chit and config.AUTO_REPLY_ENABLED and config.AUTO_REPLY_COMMENTS:
                rkey = _replied_key("comment", author)
                if _can_auto_reply(seen, rkey):
                    pending_comment_replies.append((video, author, content, chit))
                else:
                    print(f"[INFO] 评论者「{author}」已自动回复过（或在冷却中），本轮不再重复回复")
        seen[key] = {"content": content}

    save_seen(seen, seen_path)
    if new_dms == 0 and new_comments == 0:
        print(f"[INFO] 账号「{acc['name']}」本轮无新私信/新评论")

    # ---- 固定规则自动回复（复用同一个浏览器会话，不再重复扫码）----
    reply_results = []
    replied_keys: list[str] = []
    # 确定性提取成功时浏览器还没启动，这里按需再开（只有真要回复才付这个成本）
    if (pending_replies or pending_comment_replies) and browser is None:
        try:
            browser = get_browser(acc)
        except Exception as e:
            print(f"[ERROR] 无法启动浏览器发送自动回复：{e}")
    if pending_replies and browser is not None:
        for sender, text_reply in pending_replies:
            try:
                r = await asyncio.wait_for(
                    _send_fixed_reply(browser, sender, text_reply),
                    timeout=config.REPLY_TIMEOUT_S,
                )
                reply_results.append((sender, text_reply, r))
                # 仅发送成功才计入「已回复」；失败的下一轮仍会重试
                if not any(x in r for x in ("未找到会话", "需要扫码登录", "需要安全验证")):
                    replied_keys.append(_replied_key("dm", sender))
                notify(
                    "自动回复已发送（私信）",
                    f"账号「{acc['name']}」 → 「{sender}」：{text_reply}（{r}）",
                    **_notify_kwargs(acc),
                )
            except asyncio.TimeoutError:
                print(f"[WARN] 私信自动回复超时（{config.REPLY_TIMEOUT_S}s），{sender} 下一轮重试")
                notify(
                    "自动回复超时未发送（私信）",
                    f"账号「{acc['name']}」 → 「{sender}」超过 {config.REPLY_TIMEOUT_S}s 未完成，下一轮自动重试",
                    **_notify_kwargs(acc),
                )
            except Exception as e:
                print(f"[ERROR] 私信自动回复失败（{sender}）: {e}")

    if pending_comment_replies and browser is not None:
        for video, author, content, text_reply in pending_comment_replies:
            try:
                r = await asyncio.wait_for(
                    _send_comment_reply(browser, video, author, content, text_reply),
                    timeout=config.REPLY_TIMEOUT_S,
                )
                reply_results.append((author, text_reply, r))
                if not any(x in r for x in ("未找到评论", "需要扫码登录", "需要安全验证")):
                    replied_keys.append(_replied_key("comment", author))
                notify(
                    "自动回复已发送（评论）",
                    f"账号「{acc['name']}」 视频「{video}」下 {author}：{text_reply}（{r}）",
                    **_notify_kwargs(acc),
                )
            except asyncio.TimeoutError:
                print(f"[WARN] 评论自动回复超时（{config.REPLY_TIMEOUT_S}s），{author} 下一轮重试")
                notify(
                    "自动回复超时未发送（评论）",
                    f"账号「{acc['name']}」 评论者「{author}」超过 {config.REPLY_TIMEOUT_S}s 未完成，下一轮自动重试",
                    **_notify_kwargs(acc),
                )
            except Exception as e:
                print(f"[ERROR] 评论自动回复失败（{author}）: {e}")

    # 落盘「已回复」标记（关浏览器前写入，避免中途异常丢失）
    if replied_keys:
        now = time.time()
        for k in replied_keys:
            seen[k] = {"replied_at": now}
        save_seen(seen, seen_path)
        print(f"[INFO] 已记录 {len(replied_keys)} 个「已自动回复」对象，后续不再重复回复")

    try:
        if browser is not None:
            await browser.close()
    except Exception:
        pass
    _release_lock(lock_path)


async def _send_fixed_reply(browser, sender: str, text: str) -> str:
    """用已打开的浏览器给指定会话发送固定回复。返回结果文本。"""
    agent = Agent(
        task=reply.REPLY_TASK.format(url=config.CHANNELS_URL, sender=sender, reply=text),
        llm=get_llm(),
        browser=browser,
        use_vision=config.USE_VISION,
    )
    result = await agent.run()
    out = result.final_result() if hasattr(result, "final_result") else str(result)
    return (out or "").strip()[:100] or "已执行"


async def _send_comment_reply(browser, video: str, author: str, content: str, text: str) -> str:
    """用已打开的浏览器，在某条视频评论下回复固定内容。返回结果文本。"""
    agent = Agent(
        task=reply.COMMENT_REPLY_TASK.format(
            url=config.CHANNELS_URL, video=video, author=author, content=content, reply=text
        ),
        llm=get_llm(),
        browser=browser,
        use_vision=config.USE_VISION,
    )
    result = await agent.run()
    out = result.final_result() if hasattr(result, "final_result") else str(result)
    return (out or "").strip()[:100] or "已执行"


def _notify_kwargs(acc: dict) -> dict:
    """从该账号的 notify 覆盖配置中，抽出 notify() 需要的渠道参数。"""
    n = acc.get("notify") or {}
    out = {}
    for k in (
        "channel", "webhook", "webhook_type", "serverchan_sendkey", "pushplus_token",
        "wecom_corpid", "wecom_secret", "wecom_agentid", "wecom_touser",
    ):
        if k in n and n[k]:
            out[k] = n[k]
    return out


async def loop_forever() -> None:
    """持续监控循环：串行遍历所有启用账号，逐轮检查。"""
    cleaned = clear_stale_locks()
    if cleaned:
        print(f"[INFO] 启动时清理残留锁：{', '.join(cleaned)}")
    accs0 = accounts.get_enabled_accounts()
    print(
        f"[INFO] 监控账号：{', '.join(a['name'] + '(' + a['id'] + ')' for a in accs0) or '（无）'}"
    )
    while True:
        touch_heartbeat()
        accs = accounts.get_enabled_accounts()
        if not accs:
            print("[WARN] 没有启用任何账号，5 分钟后重试")
        for acc in accs:
            try:
                await run_once(acc)
            except Exception as e:
                print(f"[ERROR] 账号「{acc['name']}」本轮监控异常: {e}")
        print(f"[INFO] 等待 {config.MONITOR_INTERVAL}s 后再次检查...")
        await asyncio.sleep(config.MONITOR_INTERVAL)
