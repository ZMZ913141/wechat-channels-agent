"""打开视频号页面（扫码登录 / 手动发表）。

- 必须有头（用户要肉眼操作、扫码），固定 headless=False。
- 使用 launch_persistent_context + 账号独立 user_data_dir：
  扫码成功后登录态持久化保存，下次无需再扫码。
- 优先用本机已装的 Chrome/Edge 打开（页面兼容性、抗风控更好），
  本机没有时自动回退到 Playwright 自带 Chromium。
- 等待扫码登录成功并在控制台给出明确提示。
"""
import os
import subprocess
import time

from playwright.sync_api import sync_playwright

from . import accounts, config, loginprobe, proclock

# 视频号助手平台首页（未登录时会自动展示登录二维码）
PLATFORM_URL = "https://channels.weixin.qq.com/platform"

# 扫码最长等待时间（秒），超时只提示，不强制关闭窗口
LOGIN_WAIT_TIMEOUT = 600


def _is_logged_in_url(url: str) -> bool:
    """登录成功后 URL 会落在 /platform/xxx（登录页是 /platform/login 或跳回首页）。"""
    if "channels.weixin.qq.com" not in url:
        return False
    if "/platform" not in url:
        return False
    # 登录中间态的 URL
    if "/login" in url:
        return False
    return True


# 档案 → 实际可用的内核（实测记忆，避免每次都先失败再回退）
# 背景：账号档案是用某个浏览器建的，用另一个内核打开会因格式差异直接退出
# （实测 exitCode=21 + crashpad "Settings version is not 1"）。
# 不缓存的话每次启动都要先失败一次、白等 2-4 秒。
_WORKING_KERNEL_FILE = "working_kernels.json"


def _kernels_path() -> str:
    return os.path.join(config.STATE_DIR, _WORKING_KERNEL_FILE)


def _load_working_kernel(profile: str):
    try:
        import json
        with open(_kernels_path(), encoding="utf-8") as f:
            return (json.load(f) or {}).get(os.path.abspath(profile).lower())
    except Exception:
        return None


def _save_working_kernel(profile: str, channel: str) -> None:
    try:
        import json
        p = _kernels_path()
        os.makedirs(config.STATE_DIR, exist_ok=True)
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f) or {}
        except Exception:
            d = {}
        d[os.path.abspath(profile).lower()] = channel
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _cmd_uses_profile(cmd: str, target: str) -> bool:
    """命令行是否真的使用了某档案目录（按路径边界匹配，避免 acc2 误中 acc22）。"""
    cl = (cmd or "").lower().replace("/", "\\")
    return (target == cl
            or (target + "\\") in cl
            or (target + " ") in cl
            or (target + '"') in cl
            or cl.endswith(target))


def _profile_holder_pid(profile: str):
    """是否有 chrome/edge 进程正占用该档案（命令行含 profile 路径）。

    返回值：占用进程的 PID，或 None（没有 / 查不到）。

    ⚠️ 这是 Chromium 自带 `SingletonLock` 之外的「软检测」：proclock 是应用层
    锁，挡不住用户手动多开或上次进程被强杀。直接用进程命令行判断是否真有人
    在用这个 user_data_dir，比解析 SingletonLock 文件格式可靠（实测 headless
    下的 Chromium 根本不生成 SingletonLock 文件，纯靠文件判断会漏报）。
    ⚠️ 必须按「路径边界」匹配，否则 `profiles/acc2` 会被 `profiles/acc22`
    的前缀误当成被占用。
    """
    target = os.path.abspath(profile).lower().replace("/", "\\")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter "
             "\"Name='msedge.exe' OR Name='chrome.exe' OR Name='headless_shell.exe'\" "
             "| ForEach-Object { $_.ProcessId.ToString() + '|' + ($_.CommandLine) })"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return None
    for line in (out or "").splitlines():
        if "|" not in line:
            continue
        pid, _, cmd = line.partition("|")
        cl = (cmd or "").lower().replace("/", "\\")
        # 边界匹配：整段相等 / 后接分隔符（反斜杠、空格、引号）或位于行尾
        if _cmd_uses_profile(cmd, target):
            try:
                return int(pid)
            except (TypeError, ValueError):
                pass
    return None


def _clear_stale_singleton_lock(profile: str):
    """清理 Chromium 持久化档案残留的锁，并返回占用状态。

    返回 (已清理, 检测到活占用)：
      - (False, True)：确实有活进程在占用该档案 → **绝不**动锁（否则两个浏览器
        同时写同一档案会损坏登录态），交由上层提示用户先关窗。
      - (True, False)：没有活占用 → 删掉可能残留的 Singleton* 文件（覆盖
        「上次进程被强杀 / 窗口已关但没清理」导致的启动失败，常表现为
        exitCode=21 / 档案被占用）。
      - (False, False)：没有活占用、也没有残留锁文件，无需处理。

    ⚠️ 以「进程命令行扫描」为权威活占用信号（SingletonLock 文件在 headless
    下不一定存在）；活锁一律不碰，避免双重打开同一档案。
    """
    holder = _profile_holder_pid(profile)
    if holder and proclock._pid_alive(holder):
        return (False, True)
    removed = False
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(profile, name))
            removed = True
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return (removed, False)


def _launch(p, profile: str, channel: str | None = None, headless: bool = False):
    """启动浏览器（持久化档案）。按账号偏好选内核，失败自动回退。

    - channel：账号指定的内核（''/None=自动）。'' 表示 Playwright 内置 Chromium。
    - headless：无头模式（后台跑，不弹窗口）。发布时可开，扫码登录必须为有头。
    - 返回 (context, 实际使用的内核说明)。

    ⚠️ 档案目录**只能**通过 user_data_dir 参数传，不能往 args 里塞
    `--user-data-dir`：Playwright 内部会自己传这个命令行参数，重复指定会直接报错
    （实测报 "Pass user_data_dir parameter ... instead of specifying
    '--user-data-dir' argument"），结果浏览器一个都起不来。
    独立窗口由 persistent context 天然保证——它会开自己的进程，
    不会把窗口塞进你日常已在运行的浏览器里。

    ⚠️ 内核回退的必要性：账号档案可能是被 Edge 建的，而同一档案用内置
    Chromium 打开会因格式差异直接退出（实测 exitCode=21 + crashpad
    "Settings version is not 1"）。所以**必须按账号偏好顺序逐个回退**，
    不能只试一个就报错。
    """
    args = ["--start-maximized", "--disable-blink-features=AutomationControlled"]
    if headless:
        args.append("--disable-gpu")
    last_err = None
    # 账号偏好优先；把「上次实测能用的内核」提到最前，避免每次重复试错
    cands = accounts.channel_candidates(channel)
    wk = _load_working_kernel(profile)
    if wk is not None and wk in cands and cands.index(wk) > 0:
        cands = [wk] + [c for c in cands if c != wk]

    for ch in cands:
        try:
            kw = dict(
                user_data_dir=profile,
                headless=headless,
                args=args,
            )
            if not headless:
                # 有头时用 no_viewport 才能真正最大化；无头时 viewport 反而更稳
                kw["no_viewport"] = True
            if ch:
                kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(**kw)
            _save_working_kernel(profile, ch)
            return ctx, (ch or "chromium(内置)")
        except Exception as e:  # 本机没装该浏览器 / 档案格式不兼容 → 试下一个
            last_err = e
    raise last_err if last_err else RuntimeError("浏览器启动失败")


def _install_log_capture(account_id: str):
    """把 print 同时写到日志文件，并返回文件句柄。

    为什么需要：面板用 CREATE_NEW_CONSOLE 拉起本脚本，如果脚本因为任何错误
    提前 return，那个控制台窗口会**一闪而过**，用户根本看不到发生了什么，
    只能看到「点了没反应」。写文件后，面板可以展示，排查不再是猜。
    """
    import sys

    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        path = os.path.join(config.STATE_DIR, f"login_{account_id}.log")
        fh = open(path, "a", encoding="utf-8", buffering=1)
    except Exception:
        return None, ""

    def emit(msg: str):
        try:
            fh.write(msg + "\n")
            fh.flush()
        except Exception:
            # 文件可能已被 _finish 关闭（脚本收尾后再有输出），
            # 此时静默忽略即可，绝不能让日志失败拖垮主流程。
            pass

    # ⚠️ 必须用 sys.__stdout__（原始 stdout），绝不能用 sys.stdout ——
    # 后者即将被替换成 _Tee，若 _Tee 内部再写 sys.stdout 就形成无限递归，
    # 实测会写出 25 万字符的重复日志把磁盘塞满。
    sys.stdout = _Tee(sys.__stdout__, emit)
    return fh, path


class _Tee:
    """stdout 转发器：原样输出到控制台 + 同步落盘。"""

    def __init__(self, old, emit):
        self.old, self.emit = old, emit

    def write(self, s):
        try:
            self.old.write(s)
        except Exception:
            pass
        if s and s.strip():
            self.emit(s.rstrip())
        return len(s)

    def flush(self):
        try:
            self.old.flush()
        except Exception:
            pass

    def isatty(self):
        return False


def _finish(fh) -> None:
    """结束前补一行，确保日志末尾有明确结论（窗口要马上关了）。"""
    if not fh:
        return
    try:
        fh.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} 结束 =====\n")
        fh.flush()
        fh.close()
    except Exception:
        pass


def open_publish_page(account: dict | None = None, open_url: str | None = None) -> None:
    """启动一个【有头】浏览器，打开指定页面，等待扫码登录成功，直到用户关闭窗口。

    - account：目标账号（决定用哪个独立登录档案）；None 用当前账号。
    - open_url：要打开的页面；默认发表页（未登录会自动先出二维码）。

    ⚠️ 档案独占：一个 Chromium 档案同时只能被一个进程真正占用。本函数会先抢
    「档案锁」；抢不到时**明确报错并退出**，绝不再开一个拿不到登录态的窗口
    （那会让用户误以为「刚登录过却显示未登录」）。
    """
    acc = account or accounts.current_account()
    profile = accounts.profile_path(acc)
    url = open_url or config.PUBLISH_URL
    aid = acc["id"]
    name = acc.get("name") or aid

    # 先接管输出，让所有信息都落盘（否则控制台窗口一闪而过就没了）
    fh, log_path = _install_log_capture(aid)

    print(f"[INFO] 账号「{name}」({aid})")
    print(f"[INFO] 登录档案：{profile}")
    print(f"[INFO] 日志文件：{log_path}" if log_path else "")
    print(f"[INFO] 浏览器内核偏好：{accounts.channel_label(acc)}")

    # owner="manual"：这次打开本身就是「手动操作」，若面板已经为该账号打了
    # 暂停标记（点扫码登录时打的），要豁免自己——否则自己把自己拦在门外。
    ok, holder = proclock.acquire(
        profile, f"打开发表页（{aid}）", wait_s=45,
        respect_pause=True, owner="manual",
    )
    if not ok:
        print(f"[ERROR] 档案被占用：{proclock.holder_desc(holder)}")
        print("[HINT] 同一个浏览器档案不能被两个进程同时打开，此时硬开也拿不到登录态。")
        print("[HINT] 常见原因与处理：")
        print("[HINT]   1) 上一次打开的浏览器窗口还没关 → 直接关掉再试")
        print("[HINT]   2) 另一个账号正在发布 → 稍等它结束，或到面板看发布进度")
        print("[HINT]   3) 该账号被标记为「手动操作中」但窗口已关 → 面板点「解除暂停」")
        print("[HINT]   4) 残留锁（进程被强杀）→ 删掉 state/profile_*.lock 后重试")
        _finish(fh)
        time.sleep(6)      # 给用户看清报错再关窗
        return

    try:
        with sync_playwright() as p:
            try:
                # 启动前先清掉上次异常退出残留的浏览器锁（避免 Chromium 报
                # exitCode=21「档案被占用」）。若真有活窗口占用则直接提示关窗。
                cleared, live = _clear_stale_singleton_lock(profile)
                if live:
                    print(f"[ERROR] 该登录档案正被另一个浏览器进程（PID {_profile_holder_pid(profile)}）占用。")
                    print("[HINT] 请先关闭该账号已打开的浏览器窗口，再点一次「打开发表页」。")
                    _finish(fh)
                    time.sleep(6)
                    return
                if cleared:
                    print("[INFO] 已清理上次异常退出残留的浏览器锁，继续启动。")

                ctx, used = _launch(p, profile, accounts.channel_of(acc))
            except Exception as e:
                print(f"[ERROR] 浏览器启动失败：{e}")
                msg = str(e)
                # exitCode=21 = Chromium 的 RESULT_CODE_PROFILE_TOO_NEW：
                # 要么是「被比当前内核更新的浏览器写过」（版本不兼容），
                # 要么是「档案正被占用」。两种都要给准话，而不是泛化的 3 条提示。
                if "exitCode=21" in msg or "profile" in msg.lower() \
                        or "too new" in msg.lower() or "锁定" in msg or "占用" in msg:
                    print("[HINT] 该登录档案被比当前内核更新的浏览器写过（profile 版本不兼容），或仍被占用。")
                    print("[HINT] 解决：① 关闭该账号所有已打开的浏览器窗口后重试；")
                    print("[HINT]        ② 到「账号管理」把该账号内核设为「Microsoft Edge」"
                          "（此档案即由 Edge 创建，兼容性最好）；")
                    print("[HINT]        ③ 若仍失败，删除该账号档案目录后重新扫码登录。")
                else:
                    print("[HINT] 可能原因：")
                    print("[HINT]   1) 该档案的浏览器窗口还开着 → 全部关掉再试")
                    print("[HINT]   2) 账号指定的浏览器内核没装 → 到「账号管理」改成已装的内核"
                          "（如 msedge / chrome），或留空用内置 Chromium")
                    print("[HINT]   3) 档案目录被占用或无权限 → 关闭杀毒软件的文件占用提示后重试")
                _finish(fh)
                time.sleep(8)      # 让用户看清报错
                return
            print(f"[INFO] 浏览器已启动（内核：{used}），正在打开页面：{url}")

            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"[WARN] 页面加载提示：{e}（窗口已打开，可直接手动操作）")

            print("[INFO] 若页面出现二维码，请用微信「扫一扫」扫码并在手机上确认登录。")
            print("[INFO] 登录成功后此窗口会提示；确认无误后直接关闭浏览器窗口即可。")

            # ---- 立刻回报「这个档案现在登的是哪个视频号」----
            # 这一步很关键：两个账号如果都登成了同一个视频号，面板上一眼就能看出来。
            probe = loginprobe.probe(page)
            if probe.get("verified"):
                print(f"[OK] ✅ 已登录，当前视频号：{probe.get('nickname')}")
            elif probe.get("logged_in"):
                print(f"[WARN] 已登录但昵称未读到：{probe.get('detail')}")
            else:
                print(f"[INFO] 当前未登录（{probe.get('detail') or '请扫码'}）")
            loginprobe.write_identity(
                aid,
                {**probe, "browser": used, "ts": time.time(),
                 "at": time.strftime("%Y-%m-%d %H:%M:%S")},
            )

            # 等待：登录成功 或 用户关窗
            logged_notified = bool(probe.get("logged_in"))
            deadline = time.time() + LOGIN_WAIT_TIMEOUT
            while True:
                time.sleep(2)
                try:
                    pages = ctx.pages
                    if not pages:
                        print("[INFO] 浏览器窗口已关闭，结束。")
                        break
                    cur = pages[-1].url
                except Exception:
                    # 浏览器被用户直接关掉（进程已退出）
                    print("[INFO] 浏览器窗口已关闭，结束。")
                    break

                if _is_logged_in_url(cur):
                    if not logged_notified:
                        logged_notified = True
                        # 登录成功后重新读一次身份，写回面板
                        try:
                            pr = loginprobe.probe(page, wait_s=8)
                            loginprobe.write_identity(
                                aid,
                                {**pr, "browser": used, "ts": time.time(),
                                 "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                            )
                            print(f"[OK] ✅ 检测到登录成功！当前视频号：{pr.get('nickname') or '（读不到昵称）'}")
                        except Exception:
                            print("[OK] ✅ 检测到登录成功！")
                        print("[OK] 登录态已保存到该账号档案，下次无需再扫码。")
                        print("[INFO] 你可以继续在本窗口操作（上传视频等），完成后关闭窗口。")
                elif logged_notified:
                    # 登录态又失效/退出登录了
                    logged_notified = False
                    print("[WARN] 检测到退出登录，如需登录请重新扫码。")

                if time.time() > deadline:
                    print("[INFO] 已等待较长时间，脚本退出（浏览器窗口仍保留，可继续手动使用）。")
                    break

            try:
                ctx.close()
            except Exception:
                pass
    finally:
        proclock.release(profile)
        # 手动操作结束 → 立刻解除该账号的暂停标记，后续发布无需等到超时
        proclock.resume(aid)
        print("[INFO] 登录窗口流程结束，暂停标记已解除。")
        _finish(fh)
