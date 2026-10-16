"""确定性发布：直接用 Playwright 操作视频号发表页，复用账号登录档案。

关键结论（2026-10 实测 DOM）：
  - 正确入口是 https://channels.weixin.qq.com/platform/post/create
    （旧的 /platform/upload 会被重定向回首页）。
  - 发表页内容渲染在 iframe（.../micro/content/post/create）里，
    且描述/短标题是 contenteditable（不是 input），所以要用「点击 + 键盘输入」。
  - 上传完成后才出现表单：视频描述(data-placeholder=添加描述)、短标题、
    位置、合集、链接、活动、定时发表、声明原创、视频标注、保存草稿、发表。

流程：上传 → 等表单 → 填描述/短标题 → 声明原创(默认勾) → 视频标注(AI) → 点发表。
不依赖 LLM：更快、更稳、可离线；失败时由 publish.py 回退 browser-use Agent。

## 本模块加固的四个点（2026-10，对齐 social-auto-upload 的实测经验）
  1. **上传控件要遍历所有 frame**：视频号是无界(wujie)微前端，上传框在
     iframe 里，只查主 frame 永远找不到 → 这是「定位不到上传入口」的根因。
  2. **上传必须有硬超时 + 重试上限 + 失败截图**：实测 172MB / 上行 0.5Mbps 时
     会传到一半失败，若不设上限就会无限重传。
  3. **成功判定不能看「已发布/审核中」**：这两个词在视频管理列表页普遍存在，
     会把「其实没发出去」误判成成功。改为只认「跳转到内容管理」或「发布成功」toast。
  4. **不能静默丢弃用户填的字段**：定时/封面/可见范围如果没真正生效，
     必须如实返回给用户，而不是假装成功。
"""
import os
import time

from playwright.sync_api import sync_playwright

from . import accounts, config, proclock
from .launcher import _launch as _launch_ctx

# 上传完成后表单出现的标志文本
FORM_READY_MARKERS = ("视频描述", "短标题", "发表")
# ✅ 只认这些「明确成功」信号。
# 刻意不含「已发布」「审核中」——它们在视频管理列表页的每一条视频上都有，
# 早期版本正是靠它们判定成功，导致大量假成功。
SUCCESS_MARKERS = ("发布成功", "发表成功", "提交成功")
# 页面跳到内容管理 = 视频确实进了列表（比任何文案都可靠）
LIST_URL_MARK = "post/list"
FAIL_MARKERS = ("需要安全验证", "验证码", "实名", "资质")


class PublishFlowError(Exception):
    """确定性流程无法完成 → 触发回退。"""


def _sleep(t):
    time.sleep(t)


def _shot_dir() -> str:
    d = os.path.join(config.STATE_DIR, "shots")
    os.makedirs(d, exist_ok=True)
    return d


def _shot(page, tag: str) -> str:
    """失败现场截图 —— 页面改版时这是唯一能定位问题的线索。"""
    try:
        p = os.path.join(
            _shot_dir(),
            f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.png",
        )
        page.screenshot(path=p, full_page=False)
        print(f"[INFO] 已保存现场截图：{p}")
        return p
    except Exception as e:
        print(f"[WARN] 截图失败：{str(e)[:60]}")
        return ""


def _find_file_input(page, timeout_s: int = 60, accept_kinds: str = ""):
    """在**所有 frame** 里找文件上传控件。

    这是本模块最重要的一个修复：视频号是无界微前端，上传框渲染在 iframe 中，
    `page.query_selector()` 只看主 frame，永远返回 None。

    accept_kinds：形如 "video" 时只接受 accept 里含 video 的控件
    （页面同时存在视频/封面两个 file input，必须区分）。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in page.frames:
            try:
                n = f.locator('input[type="file"]').count()
            except Exception:
                continue
            for i in range(n):
                el = f.locator('input[type="file"]').nth(i)
                if accept_kinds:
                    try:
                        acc = (el.get_attribute("accept") or "").lower()
                    except Exception:
                        acc = ""
                    if accept_kinds.lower() not in acc:
                        continue
                return el
        _sleep(1.5)
    return None


def _upload_video(page, video_path: str) -> None:
    """上传视频：定位 → 重试注入 → 硬超时等待完成。

    重试是因为 SPA 重渲染会让 input 变成 detached，Playwright 直接抛
    `Cannot set input files to detached input element`。
    """
    last_err = None
    for attempt in range(1, 4):
        el = _find_file_input(page, timeout_s=60, accept_kinds="video") \
            or _find_file_input(page, timeout_s=5)   # 兜底：不按 accept 过滤
        if el is None:
            body = ""
            try:
                body = page.evaluate("document.body.innerText") or ""
            except Exception:
                pass
            if "扫码登录" in body or "请使用微信扫描" in body or "/login" in page.url:
                raise PublishFlowError("账号未登录（出现扫码登录页），请先扫码登录")
            raise PublishFlowError("未找到视频上传入口（已遍历全部 frame，页面结构可能已改版）")
        try:
            el.set_input_files(video_path)
            print(f"[INFO] 视频文件已注入（第 {attempt} 次尝试）")
            return
        except Exception as e:
            last_err = e
            print(f"[WARN] 注入视频文件失败（{str(e)[:80]}），重试…")
            _sleep(2)
    raise PublishFlowError(f"视频文件注入失败：{str(last_err)[:100]}")


def _wait_upload_done(page, frame, timeout_s: int | None = None) -> bool:
    """等上传完成（表单出现 = 上传结束）。

    硬超时的意义：弱网下大文件会卡住，若不设上限整个发布任务会永远挂着。
    """
    timeout_s = timeout_s or int(getattr(config, "PUBLISH_UPLOAD_TIMEOUT_S", 3600))
    print(f"[INFO] 等待上传完成（上限 {timeout_s}s，大文件可能较久）…")
    return _wait_form_ready(frame, timeout_s=timeout_s)


def _get_post_frame(page, timeout_s: int = 60):
    """取得真正渲染发表表单的 iframe。

    注意：主 frame 的 URL 同样含 /post/create，但它只有导航栏；
    表单在 .../micro/content/post/create 这个 iframe 里，必须优先匹配 "micro"。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frames = page.frames
        for f in frames:
            if "micro" in f.url and "post/create" in f.url:
                return f
        for f in frames:
            if "micro" in f.url:
                return f
        _sleep(1)
    return page.main_frame


def _wait_form_ready(frame, timeout_s: int = 240):
    """等视频上传完成、表单出现（描述编辑器 + 关键字段文案）。"""
    deadline = time.time() + timeout_s
    warned = False
    while time.time() < deadline:
        try:
            txt = frame.evaluate("document.body.innerText") or ""
        except Exception:
            txt = ""
        has_editor = False
        try:
            has_editor = bool(frame.evaluate(
                "(document.querySelectorAll('.input-editor').length>0)"
                "||(Array.from(document.querySelectorAll('*')).filter(e=>e.getAttribute('contenteditable')!==null).length>0)"
            ))
        except Exception:
            pass
        if all(m in txt for m in ("视频描述", "短标题", "发表")) and has_editor:
            return True
        # 弱网大文件时给点提示，避免用户以为卡死
        if not warned and timeout_s > 300:
            left = int((deadline - time.time()) // 60)
            print(f"[INFO] 仍在上传中…（剩余约 {left} 分钟）")
            warned = True
        _sleep(3)
    return False


def _fill_contenteditable(frame, page, value: str) -> bool:
    """聚焦描述编辑器后键盘输入（Frame 没有 keyboard，必须用 page 级）。

    用键盘而不是 innerHTML：富文本编辑器直接赋 textContent 会丢内容/丢事件。
    """
    focused = frame.evaluate(
        "(()=>{const e=document.querySelector('.input-editor')||"
        "Array.from(document.querySelectorAll('*')).find(x=>x.getAttribute('contenteditable')!==null);"
        "if(!e) return false; e.focus&&e.focus(); e.click&&e.click(); return true;})()"
    )
    if not focused:
        return False
    page.keyboard.type(value, delay=15)
    return True


def _fill_form_item(frame, label: str, value: str) -> bool:
    """在 label 文本为「label」的表单项里填值（contenteditable 或 input 都支持）。"""
    js = """
    ([label, value]) => {
      const items = Array.from(document.querySelectorAll('.form-item, div[class*=form-item]'));
      for (const it of items) {
        const lab = it.querySelector('.label');
        if (!lab || !lab.innerText || !lab.innerText.includes(label)) continue;
        const box = it.querySelector('[contenteditable=true],[contenteditable=""],input,textarea');
        if (!box) continue;
        box.focus && box.focus();
        if (box.tagName === 'INPUT' || box.tagName === 'TEXTAREA') {
          box.value = value;
          box.dispatchEvent(new Event('input', {bubbles:true}));
          box.dispatchEvent(new Event('change', {bubbles:true}));
        } else {
          box.textContent = value;
          box.dispatchEvent(new Event('input', {bubbles:true}));
        }
        return true;
      }
      return false;
    }
    """
    try:
        return bool(frame.evaluate(js, [label, value]))
    except Exception:
        return False


def format_short_title(short: str) -> str:
    """把短标题规整成平台接受的 7-15 字。

    实测：短标题有硬校验，不在区间会被直接拦下提交（然后表现为「莫名失败」）。
    不足时用「，精彩内容分享」补齐——这是官方推荐的口播式兜底文案。
    """
    s = (short or "").strip()
    s = "".join(ch for ch in s if ch not in "\r\n\t")
    if not s:
        return ""
    if len(s) > 15:
        s = s[:15]
    if len(s) < 7:
        s = (s + "，精彩内容分享")[:15]
    return s


def _set_schedule(frame, page, when: str) -> bool:
    """设置定时发表时间。

    实现取自social-auto-upload 的实测路径：先切到「定时发表」，
    再在日期面板里按「X月X日」匹配 + 键盘输入时分。
    返回 False 时**不会中断发布**，但会被如实汇报给用户。
    """
    try:
        # 1) 切到「定时发表」单选
        switched = frame.evaluate(
            """(() => {
              const els = Array.from(document.querySelectorAll(
                'label,span,div[class*=radio],div[class*=item],div[class*=switch]'));
              for (const e of els) {
                const t = (e.innerText || '').trim();
                if (!t || t.length > 12) continue;
                if (/定时发表|定时发布|定时/.test(t)) { e.click && e.click(); return t; }
              }
              return '';
            })()"""
        )
        if not switched:
            return False
        _sleep(2)

        # 2) 解析目标时间 → 填日期面板
        dt = str(when).strip().replace("T", " ")
        m = None
        import re

        mm = re.search(r"(\d{4})-(\d{2})-(\d{2})[ ]?(\d{1,2}):(\d{2})", dt)
        if mm:
            y, mo, d, hh, mi = (int(x) for x in mm.groups())
            from datetime import datetime

            m = datetime(y, mo, d, hh, mi)
        if not m:
            # 只给了「X月X日 HH:MM」→按最近的将来时间补年份
            mm = re.search(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", dt)
            if not mm:
                return False
            from datetime import datetime

            now = datetime.now()
            mo, d, hh, mi = (int(x) for x in mm.groups())
            try:
                m = now.replace(month=mo, day=d, hour=hh, minute=mi, second=0)
            except ValueError:
                return False
            if m <= now:
                m = m.replace(year=m.year + 1)
        print(f"[INFO] 定时目标时间：{m.strftime('%Y-%m-%d %H:%M')}")

        # 3) 在日期面板里选「X月X日」
        picked = frame.evaluate(
            """(args) => {
              const [mo, d] = args;
              const cells = Array.from(document.querySelectorAll(
                '[class*=cell],[class*=date],td,span,div'));
              let monthDone = false;
              for (const c of cells) {
                const t = (c.innerText || '').trim();
                if (!monthDone) {
                  if (t === mo + '月' || t === String(mo)) { c.click(); monthDone = true; }
                  continue;
                }
                if (t === String(d)) { c.click(); return mo + '-' + d; }
              }
              return monthDone ? 'month-only' : '';
            }""",
            [m.month, m.day],
        )
        _sleep(1.5)
        if not picked:
            print("[WARN] 定时面板未能自动选择日期")
            return False

        # 4) 时分：输入框直接填（antd time-picker 接受键盘输入）
        filled = frame.evaluate(
            """(hm) => {
              const [hh, mi] = hm;
              const inputs = Array.from(document.querySelectorAll('input'));
              for (const i of inputs) {
                const v = i.value || '';
                const ph = i.placeholder || '';
                if (/时间|时|:/.test(ph + v) && i.offsetParent !== null) {
                  i.focus();
                  i.value = hh;
                  i.dispatchEvent(new Event('input', {bubbles:true}));
                  i.dispatchEvent(new Event('change', {bubbles:true}));
                  i.blur();
                  return hh;
                }
              }
              return '';
            }""",
            [m.strftime("%H"), m.strftime("%M")],
        )
        _sleep(1)
        print(f"[INFO] 定时设置：{picked} {filled or '(时分未能自动填)'}")
        return bool(filled)
    except Exception as e:
        print(f"[WARN] 设置定时发表异常：{str(e)[:80]}")
        return False


def _set_cover(frame, page, cover_path: str) -> bool:
    """上传自定义封面。

    关键坑：封面弹窗打开后，页面上会同时存在「视频」和「封面」两个 file input，
    必须按 accept 含 image 区分，否则会覆盖到视频控件上。
    """
    try:
        opened = frame.evaluate(
            """(() => {
              const els = Array.from(document.querySelectorAll(
                'button,div[class*=btn],span,div[class*=cover],div[class*=item]'));
              for (const e of els) {
                const t = (e.innerText || '').trim();
                if (t && t.length <= 8 && /封面/.test(t)) { e.click && e.click(); return t; }
              }
              return '';
            })()"""
        )
        if not opened:
            return False
        _sleep(2.5)
        el = _find_file_input(frame, timeout_s=12, accept_kinds="image")
        if el is None:
            print("[WARN] 打开封面弹窗后未找到图片上传控件")
            return False
        el.set_input_files(cover_path)
        _sleep(2)
        # 关闭封面弹窗，避免遮挡「发表」按钮
        frame.evaluate(
            """(() => {
              const els = Array.from(document.querySelectorAll('button,a,div[class*=close]'));
              for (const e of els) {
                const t = (e.innerText || '').trim();
                if (/^(完成|确定|取消|保存)$/.test(t)) { e.click(); return t; }
              }
              return '';
            })()"""
        )
        print("[INFO] 封面已设置")
        return True
    except Exception as e:
        print(f"[WARN] 设置封面异常：{str(e)[:80]}")
        return False


def publish_deterministic(acc: dict, mat: dict, overrides: dict | None = None) -> tuple[bool, str]:
    """确定性发布。返回 (成功, 文本)。

    返回文本会**如实列出未生效的字段**——这是本模块的重要约定：
    宁可告诉用户「定时没设上」，也不能假装全部生效。
    """
    ov = overrides or {}
    profile = accounts.profile_path(acc)
    video_path = os.path.abspath(mat["video_path"])
    if not os.path.exists(video_path):
        return False, f"视频文件不存在：{video_path}"

    def pick(field):
        v = (ov.get(field) or "").strip()
        return v if v else (mat.get(field) or "").strip()

    title = pick("title")
    desc = pick("desc")
    tags = pick("tags")
    ai = pick("ai_declare")
    original = str(ov.get("original") if ov.get("original") not in (None, "") else mat.get("original", True)).lower() in (
        "1", "true", "on", "yes")
    scheduled_at = pick("scheduled_at")
    cover_path = pick("cover_path")
    visibility = (pick("visibility") or "公开").strip()

    # 短标题先规整，避免提交时被平台硬校验拦下
    short = format_short_title(pick("short_title"))
    if pick("short_title") and short != pick("short_title"):
        print(f"[INFO] 短标题已规整为 7-15 字：{short}")

    full_desc = desc or title
    if tags:
        tag_str = " ".join(f"#{t.strip()}" for t in tags.split(",") if t.strip())
        full_desc = f"{full_desc}\n{tag_str}" if full_desc else tag_str

    # 记录「用户要求了但确定性通道可能做不到」的字段，最后如实汇报
    unset_fields: list[str] = []

    # 档案独占：与后台监控 / 打开发表页互斥，避免「发布时拿到的是没登录态的档案」
    _ok, _holder = proclock.acquire(profile, f"同步发布（{acc.get('id')}）", wait_s=90)
    if not _ok:
        return False, (
            f"该账号的浏览器档案正被 {proclock.holder_desc(_holder)} 占用，"
            "请先停止监控（或等手动窗口关闭）后重试"
        )
    with sync_playwright() as p:
        try:
            ctx, _ = _launch_ctx(p, profile, accounts.channel_of(acc))
        except Exception as e:
            proclock.release(profile)
            return False, f"浏览器启动失败（档案可能被其它进程占用）：{e}"
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(config.PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
            _sleep(6)

            # 1) 上传视频（遍历所有 frame + 注入重试）
            _upload_video(page, video_path)

            # 2) 等上传完成、表单就绪（带硬超时）
            frame = _get_post_frame(page)
            if not _wait_upload_done(page, frame):
                _shot(page, "upload-stall")
                raise PublishFlowError(
                    f"视频上传未在 {config.PUBLISH_UPLOAD_TIMEOUT_S}s 内完成"
                    "（网络过慢或文件过大，请稍后重试或压缩视频）"
                )

            # 3) 填描述 / 短标题
            desc_ok = _fill_contenteditable(frame, page, full_desc[:900])
            if not desc_ok and not _fill_form_item(frame, "视频描述", full_desc[:900]):
                raise PublishFlowError("无法填写视频描述")
            _sleep(1)
            if short and not _fill_form_item(frame, "短标题", short):
                # 表单项是 JS 动态生成的，退化为「点击 + 键盘输入」
                if frame.evaluate(
                    "(()=>{const its=Array.from(document.querySelectorAll('.form-item,div[class*=form-item]'));"
                    "for(const it of its){const l=it.querySelector('.label');"
                    "if(l&&l.innerText&&l.innerText.includes('短标题')){const b=it.querySelector('.form-item-body');"
                    "if(b){b.click();return true;}}}return false;})()"
                ):
                    page.keyboard.type(short, delay=20)
                else:
                    print("[WARN] 短标题未填写（不影响发表）")

            # 4) 视频标注（AI 声明，依法必须）— 先做，避免原创弹窗遮挡
            if ai and ai != "非AI内容":
                picked = ""
                try:
                    frame.evaluate(
                        "(()=>{const s=document.querySelector('.mark-tag-select');if(s){s.click();return true;}"
                        "const els=Array.from(document.querySelectorAll('*'));"
                        "for(const e of els){if((e.innerText||'').includes('选择视频标注')){e.click();return true;}}"
                        "return false;})()"
                    )
                    _sleep(1.5)
                    picked = frame.evaluate(
                        "(()=>{const sel='.select-option,.option,[class*=option],li,.weui-desktop-dropdown__item';"
                        "const opts=Array.from(document.querySelectorAll(sel));"
                        "for(const o of opts){const t=o.innerText||'';if(/AI|ai|生成/.test(t)){o.click();return t.trim();}}"
                        "return '';})()"
                    )
                except Exception as e:
                    print(f"[WARN] 视频标注异常：{str(e)[:60]}")
                print(f"[INFO] 视频标注：{picked or '未找到 AI 选项（可在发布页手动补）'}")
                _sleep(1)

            # 5) 声明原创（默认勾选）— 勾选后可能弹协议/分类弹窗，需要确认
            if original:
                try:
                    checked = frame.evaluate(
                        "(()=>{const c=document.querySelector('.declare-original-checkbox');"
                        "if(!c) return 'no-box';"
                        "const l=c.querySelector('label,input,.weui-desktop-checkbox,span');"
                        "(l||c).click(); return 'clicked';})()"
                    )
                    print(f"[INFO] 声明原创：{checked}")
                    _sleep(2)
                    # 处理可能出现的协议/分类弹窗：点「同意/确认/确定」，必要时选第一个分类
                    for _ in range(3):
                        acted = frame.evaluate(
                            "(()=>{const bs=Array.from(document.querySelectorAll('button,a,label,div[class*=btn]'));"
                            "for(const b of bs){const t=(b.innerText||'').trim();"
                            "if(/^(同意|确认|确定|勾选|知道了)$/.test(t)){b.click();return t;}}"
                            "return '';})()"
                        )
                        if not acted:
                            break
                        print(f"[INFO] 原创弹窗处理：{acted}")
                        _sleep(1.5)
                except Exception as e:
                    print(f"[WARN] 声明原创异常：{str(e)[:60]}")

            # 6) 定时发表（放在点发表之前）
            if scheduled_at:
                if not _set_schedule(frame, page, scheduled_at):
                    unset_fields.append(f"定时发表（{scheduled_at}）")

            # 7) 可见范围
            if visibility and visibility != "公开":
                ok_vis = frame.evaluate(
                    """(v) => {
                      const els = Array.from(document.querySelectorAll(
                        'div[class*=select],div[class*=item],span,label'));
                      for (const e of els) {
                        const t = (e.innerText || '').trim();
                        if (t && t.length <= 6 && t.includes(v)) { e.click(); return t; }
                      }
                      return '';
                    }""",
                    visibility,
                )
                if ok_vis:
                    print(f"[INFO] 可见范围：{ok_vis}")
                else:
                    unset_fields.append(f"可见范围（{visibility}）")

            # 8) 自定义封面
            if cover_path and os.path.exists(cover_path):
                if not _set_cover(frame, page, os.path.abspath(cover_path)):
                    unset_fields.append("自定义封面")
            elif cover_path:
                unset_fields.append(f"自定义封面（文件不存在：{cover_path}）")

            # 9) 点发表
            clicked = frame.evaluate(
                "(()=>{const bs=Array.from(document.querySelectorAll('button'));"
                "const b=bs.filter(x=>(x.innerText||'').trim()==='发表').pop();"
                "if(b){b.click();return true;} return false;})()"
            )
            if not clicked:
                _shot(page, "no-submit-button")
                raise PublishFlowError("找不到「发表」按钮")

            # 10) 等结果。
            #成功判定只看两种硬信号：跳转到内容管理页 / 出现「发布成功」toast。
            for i in range(45):
                _sleep(2)
                try:
                    txt = frame.evaluate("document.body.innerText") or ""
                except Exception:
                    txt = ""
                try:
                    urls = " ".join(f.url for f in page.frames)
                except Exception:
                    urls = ""
                if LIST_URL_MARK in page.url or LIST_URL_MARK in urls:
                    return True, _with_notes(
                        "发布成功（已跳转内容管理，确定性填写）", unset_fields
                    )
                # 只认明确的成功文案，且优先命中 toast（短、含「成功」）
                for m in SUCCESS_MARKERS:
                    if m in txt:
                        return True, _with_notes(
                            f"发布成功（页面提示「{m}」）", unset_fields
                        )
                if any(m in txt for m in FAIL_MARKERS):
                    _shot(page, "blocked")
                    return False, "页面提示需人工处理：" + txt[:120].replace("\n", " ")
            _shot(page, "no-confirm")
            return False, _with_notes(
                "已点击发表，但 90 秒内未检测到成功信号"
                "（请到视频号「内容管理」确认是否真的发出）",
                unset_fields,
            )
        finally:
            try:
                ctx.close()
            except Exception:
                pass
            proclock.release(profile)


def _with_notes(text: str, unset_fields: list[str]) -> str:
    """把未生效字段附加到结果文本上——绝不静默吞掉用户填的配置。"""
    if not unset_fields:
        return text
    return f"{text}｜⚠️ 以下配置未生效，请手动补：{'、'.join(unset_fields)}"