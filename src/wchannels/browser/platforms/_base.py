"""平台发布适配器：公共基类、输入/输出数据结构、跨 iframe 工具。

设计：每个平台一个 Publisher 子类，统一入口 publish(ctx, input) -> PublishOutcome。
视频号已完整实现；抖音/小红书按公开调研的最佳实践落地选择器与流程，
但各平台页面改版频繁，选择器需以真实账号在真机/真浏览器上做一轮校验
（本仓库无法离线验证，已在各适配器标注 TODO 校验点）。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from ...config import settings
from ..media_probe import validate_video, VideoValidationError


@dataclass
class PublishInput:
    video_path: str
    short_title: str = ""
    description: str = ""
    topics: str = ""            # 逗号分隔的话题
    ai_disclosure: str = ""     # AI 标识文案
    collection: str = ""        # 合集名
    cover_path: str = ""        # 自定义封面（可选）
    scheduled_at: datetime | None = None
    original_work: bool = False # 是否声明原创（平台支持才生效）
    visibility: str = "public"  # 可见范围：public 公开 / private 仅自己可见


@dataclass
class PublishOutcome:
    ok: bool
    message: str
    screenshot: str = ""


def normalize_topics(topics: str) -> list[str]:
    """把用户/AI 给的话题字符串归一化成干净列表。

    兼容输入："#女装 #穿搭"、"女装,穿搭"、"女装，穿搭"、混用分隔、
    带 # 号、首尾空格、重复项。各平台适配器拿到的统一是不带 # 的词，
    需要 # 的平台（小红书文本兜底）自行拼接，避免格式在多处发散。
    """
    if not topics:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in topics.replace("，", ",").replace(" ", ",").split(","):
        t = raw.strip().lstrip("#").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def iter_frames(page):
    """页面 + 它包含的所有 iframe（视频号是微前端，表单在 iframe 里）。"""
    yield page
    try:
        for f in page.frames:
            yield f
    except Exception:
        pass


def retry(fn, times: int = 2, backoff_ms: int = 2000, page=None):
    """对瞬时失败做有限重试（带递增间隔）。返回函数结果，最终失败抛最后一次异常。

    只用于「可安全重来」的动作（打开页面、上传文件）。
    绝不能包「点发布」——重试点发布可能把同一条视频发两遍。
    """
    last: Exception | None = None
    for attempt in range(times):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt < times - 1 and page is not None:
                try:
                    page.wait_for_timeout(backoff_ms * (attempt + 1))
                except Exception:
                    pass
    raise last  # type: ignore[misc]


def page_digest(page) -> str:
    """失败诊断快照：当前 URL + 页面标题，帮助判断是掉登录还是改版。"""
    try:
        url = page.url or ""
    except Exception:
        url = ""
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    parts = []
    if url:
        parts.append(f"页面地址 {url}")
    if title:
        parts.append(f"页面标题「{title}」")
    return "；".join(parts)


def find_file_input(page):
    """找上传控件（页面里第一个 input[type=file]）。"""
    for el in _all_file_inputs(page):
        return el
    return None


def _all_file_inputs(page) -> list:
    """按 frame 顺序收集页面上所有 input[type=file]。

    直接遍历 page.frames（它已含主文档 frame，每个 frame 只出现一次）。
    不能走 iter_frames——那个会额外 yield page，导致主文档被遍历两遍、
    控件被重复计数，从而骗过 _pick_cover_input 的「≥2 取最后一个」判断。
    """
    out = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for fr in frames:
        try:
            out.extend(fr.query_selector_all('input[type="file"]'))
        except Exception:
            continue
    return out


def _pick_cover_input(page):
    """为封面挑一个安全的 file input。

    不能用「元素对象 != 视频控件」来判断：Playwright 每次查询都返回新的
    ElementHandle 实例，同一 DOM 节点也不相等，会把封面图塞回视频控件、
    覆盖掉刚上传好的视频。这里改用两条确定性线索：
    1) accept 里含 image 的控件（平台封面控件的典型特征）；
    2) 没有特征时，取「最后一个」file input——封面控件是点「更换封面」后才出现的。
    只有一个控件时（那就是视频控件），宁可不上传也不能冒覆盖视频的风险。
    """
    inputs = _all_file_inputs(page)
    if not inputs:
        return None
    try:
        for el in inputs:
            accept = (el.get_attribute("accept") or "").lower()
            if "image" in accept:
                return el
    except Exception:
        pass
    if len(inputs) >= 2:
        return inputs[-1]
    return None


def upload_cover(page, cover_path: str) -> bool:
    """上传自定义封面（best-effort，三平台通用）。

    各平台封面控件形态不一：常见做法是点「封面/更换封面」后，
    页面再冒出一个图片用的 input[type=file]。找不到就跳过，不阻断发布。
    """
    if not cover_path or not os.path.exists(cover_path):
        return False
    # 点开封面设置入口，把图片用的 file input 唤出
    for sel in ('button:has-text("更换封面")', 'button:has-text("上传封面")',
                'span:has-text("自定义封面")', 'div:has-text("封面")'):
        try:
            el = None
            for fr in iter_frames(page):
                el = fr.query_selector(sel)
                if el:
                    break
            if not el:
                continue
            el.click()
            page.wait_for_timeout(800)
            img_input = _pick_cover_input(page)
            if img_input:
                img_input.set_input_files(cover_path)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def screenshot(page, tag: str) -> str:
    from datetime import datetime as _dt
    p = settings.shots_dir / f"fail_{_dt.now().strftime('%Y%m%d_%H%M%S')}_{tag}.png"
    try:
        page.screenshot(path=str(p), full_page=False)
        return str(p)
    except Exception:
        return ""


class Publisher(ABC):
    platform: str = ""
    publish_url: str = ""
    upload_wait_ms: int = 8000
    title_min: int = 0
    title_max: int = 999

    # ---------- 公共流程 ----------
    def _wait_file_input(self, page, timeout_s: int = 25):
        """等待上传控件出现。视频号是 wujie 微前端，3 秒内常常还没挂载完，
        早查一次就判「找不到上传控件」会误报；这里轮询到超时为止。"""
        from time import monotonic, sleep
        end = monotonic() + timeout_s
        while monotonic() < end:
            fi = find_file_input(page)
            if fi:
                return fi
            sleep(1)
        return None

    def publish(self, ctx, inp: PublishInput) -> PublishOutcome:
        # 文件预检
        try:
            validate_video(inp.video_path)
        except VideoValidationError as e:
            return PublishOutcome(False, f"预检未过：{e}")

        title = (inp.short_title or inp.description)[: self.title_max]
        if self.title_min and len(title) < self.title_min:
            return PublishOutcome(False, f"标题过短（{len(title)} 字），至少 {self.title_min} 字")

        # 话题格式统一收敛：各适配器拿到的都是去 # 去重后的逗号分隔串
        inp.topics = ",".join(normalize_topics(inp.topics))

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            retry(lambda: page.goto(self.publish_url, wait_until="domcontentloaded", timeout=60000),
                  times=2, backoff_ms=3000, page=page)
        except Exception as e:
            return PublishOutcome(False, f"打开发表页失败：{e}（{page_digest(page)}）", screenshot(page, "goto"))

        page.wait_for_timeout(3000)

        # 钩子：部分平台（抖音/小红书）落地页没有上传控件，需先点「发布视频」进入
        try:
            self.enter_publish_page(page)
        except Exception:
            pass

        def _upload_once():
            # 每次重试都重新查找：视频号（wujie 微前端）上传后会把 input 重渲染，
            # 复用旧 ElementHandle 会报 "detached element"，上传永远失败。
            fi = find_file_input(page)
            if not fi:
                raise RuntimeError("找不到上传控件（页面可能改版或登录已失效）")
            try:
                fi.set_input_files(inp.video_path)
            except Exception:
                # 兜底：元素已 detached 时改用 file chooser（点 input 再塞文件）
                fi2 = find_file_input(page)
                if not fi2:
                    raise
                with page.expect_file_chooser(timeout=8000) as fc:
                    fi2.evaluate("el => el.click()")
                fc.value.set_files(inp.video_path)

        if not self._wait_file_input(page):
            return PublishOutcome(False, f"找不到上传控件（页面可能改版或登录已失效；{page_digest(page)}）",
                                  screenshot(page, "no_file"))
        try:
            retry(_upload_once, times=2, backoff_ms=2000, page=page)
        except Exception as e:
            return PublishOutcome(False, f"上传失败：{e}（{page_digest(page)}）", screenshot(page, "upload"))

        page.wait_for_timeout(self.upload_wait_ms)

        try:
            self.fill_form(page, inp, title)
        except Exception as e:
            return PublishOutcome(False, f"填写表单失败：{e}（{page_digest(page)}）", screenshot(page, "fill"))

        # 自定义封面（三平台通用，best-effort，失败不阻断发布）
        if inp.cover_path:
            try:
                upload_cover(page, inp.cover_path)
            except Exception:
                pass

        # 私密模式安全闸：必须确认「仅自己可见」已选中才允许提交。
        # 平台没有该控件（如视频号网页版）或选择器改版时一律中止，
        # 宁可这条不发，也不能把本该私密的视频公开发出去。
        if (inp.visibility or "public").lower() == "private":
            try:
                confirmed = self.set_visibility(page, "private")
            except Exception:
                confirmed = False
            if not confirmed:
                return PublishOutcome(
                    False,
                    "无法确认『仅自己可见』已选中，为避免公开发布已中止（未提交）",
                    screenshot(page, "visibility"),
                )

        try:
            self.click_publish(page)
        except Exception as e:
            return PublishOutcome(False, f"点发布失败：{e}（{page_digest(page)}）", screenshot(page, "click"))

        ok, msg = self.verify(page)
        if not ok:
            return PublishOutcome(False, msg, screenshot(page, "verify"))
        return PublishOutcome(True, msg)

    # ---------- 工具（子类复用） ----------
    @staticmethod
    def first(page, selector):
        """跨 frame 找第一个命中的元素。

        selector 支持传入候选列表 ['selA', 'selB']：按顺序逐个尝试，
        单个选择器写法过期/非法不会拖垮其它候选（平台改版时的第一道缓冲）。
        """
        candidates = [selector] if isinstance(selector, str) else list(selector)
        for sel in candidates:
            for fr in iter_frames(page):
                try:
                    el = fr.query_selector(sel)
                    if el:
                        return el
                except Exception:
                    continue
        return None

    @staticmethod
    def fill_text(page, selector, text):
        el = Publisher.first(page, selector)
        if not el:
            raise RuntimeError(f"找不到字段：{selector}")
        el.fill(text)

    @staticmethod
    def click(page, selector):
        el = Publisher.first(page, selector)
        if not el:
            raise RuntimeError(f"找不到可点击元素：{selector}")
        el.click()

    # ---------- 子类可选覆写 ----------
    def enter_publish_page(self, page) -> None:
        """落地页需要额外点击进入发布表单的平台覆写此方法。默认无操作。"""
        return None

    def fetch_collections(self, ctx) -> list[str]:
        """拉取该账号平台的合集列表，供前端下拉选择。

        默认返回空（平台未实现时前端降级为手动输入合集名）。
        实现方在 ctx.pages[0] 上打开合集页/发表页读取下拉项。
        """
        return []

    def set_visibility(self, page, visibility: str) -> bool:
        """把「可见范围」切到指定值，返回是否已确认选中。

        只处理 private（仅自己可见）：在页面里找文案为「仅自己可见/私密」的
        单选项，点选后用 data-checked / aria-checked / 内部 input 状态复核。
        找不到或无法确认一律返回 False——调用方据此中止发布，绝不猜测。
        """
        if visibility != "private":
            return True
        marks = ("仅自己可见", "私密", "仅我可见")
        for fr in iter_frames(page):
            try:
                els = fr.query_selector_all('label, [role="radio"], [role="option"], li, button')
            except Exception:
                continue
            # 优先点文案最短的（真正的选项文案是「仅自己可见」，
            # 外层容器的 innerText 会拼上「公开/好友可见」等，长度更长）
            cands = []
            for el in els:
                try:
                    txt = (el.inner_text() or "").strip().replace(" ", "")
                except Exception:
                    continue
                if len(txt) > 20 or not any(m in txt for m in marks):
                    continue
                cands.append((len(txt), el))
            cands.sort(key=lambda x: x[0])
            for _, el in cands:
                try:
                    if el.get_attribute("data-checked") == "true":
                        return True
                    if (el.get_attribute("aria-checked") or "").lower() == "true":
                        return True
                    el.click()
                    page.wait_for_timeout(500)
                    if el.get_attribute("data-checked") == "true":
                        return True
                    if (el.get_attribute("aria-checked") or "").lower() == "true":
                        return True
                    cb = el.query_selector('input[type="checkbox"], input[type="radio"]')
                    if cb and cb.is_checked():
                        return True
                except Exception:
                    continue
        return False

    # ---------- 子类实现 ----------
    @abstractmethod
    def fill_form(self, page, inp: PublishInput, title: str) -> None:
        ...

    @abstractmethod
    def click_publish(self, page) -> None:
        ...

    @abstractmethod
    def verify(self, page) -> tuple[bool, str]:
        ...
