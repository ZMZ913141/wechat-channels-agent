"""小红书发布适配器（选择器基于公开调研，页面改版频繁，需真实账号真机校验）。

流程：创作服务平台发布页直达视频发布 → 上传 → 标题(≤20 字) / 正文 /
话题(chip，需点选推荐项) / 更多设置(AI 声明、合集、定时)。
成功判定：出现「发布成功」文案或跳转到笔记管理。

字段完整性：上传 / 标题 / 描述 / 话题(chip+文本兜底) / 封面(基类通用) 已实现；
AI 标识 / 合集 / 定时为 best-effort 骨架，选择器标注 [真机校验]。
"""
from ._base import Publisher, PublishOutcome, find_file_input

PUBLISH_URL = ("https://creator.xiaohongshu.com/publish/publish"
               "?source=official&from=tab_switch&target=video")
PUBLISH_BUTTON = 'button:has-text("发布")'
SUCCESS_TEXT = "发布成功"
TITLE_INPUT = 'input[placeholder*="标题"], textarea[placeholder*="标题"]'
DESC_INPUT = 'div[contenteditable="true"][placeholder*="正文"], div[class*="content"]'
TOPIC_BTN = 'button:has-text("话题"), span:has-text("话题")'
TOPIC_INPUT = 'input[placeholder*="搜索话题"], input[placeholder*="话题"]'
TOPIC_SUGGEST = 'li[role="option"], div[class*="suggestion"] >> nth=0'
UPLOAD_TRIGGER = 'div:has-text("上传视频"), button:has-text("上传视频"), span:has-text("点击上传")'
# [真机校验] 更多设置区骨架，DOM 随版本变化，均 best-effort
MORE_SETTINGS = 'div:has-text("更多设置"), span:has-text("更多设置")'
AI_DISCLOSURE_SWITCH = 'div:has-text("AI生成"), [role="switch"]'
COLLECTION_TRIGGER = 'div:has-text("合集"), span:has-text("添加合集")'
SCHEDULE_TOGGLE = 'div:has-text("定时发布"), label:has-text("定时发布")'


class XiaohongshuPublisher(Publisher):
    platform = "xiaohongshu"
    publish_url = PUBLISH_URL
    upload_wait_ms = 10000
    title_min = 0
    title_max = 20

    def enter_publish_page(self, page) -> None:
        # 直达页若尚未出现上传控件，先点一次上传区域把 file input 唤出
        if find_file_input(page) is None:
            try:
                btn = self.first(page, UPLOAD_TRIGGER)
                if btn:
                    btn.click()
                    page.wait_for_timeout(2000)
            except Exception:
                pass

    def fill_form(self, page, inp, title: str) -> None:
        # 标题
        try:
            self.fill_text(page, TITLE_INPUT, title)
        except Exception:
            pass

        # 描述（best-effort）
        try:
            el = self.first(page, DESC_INPUT)
            if el:
                el.click()
                el.fill(inp.description or "")
        except Exception:
            pass

        # 话题：小红书话题是 chip，优先点「话题」→输入关键词→选推荐项；
        # 选不中再退到正文里直接写 #话题（平台可能不成链，仅文本兜底）
        if inp.topics:
            chips_written = False
            try:
                for t in [x.strip() for x in inp.topics.split(",") if x.strip()]:
                    btn = self.first(page, TOPIC_BTN)
                    if not btn:
                        break
                    btn.click()
                    page.wait_for_timeout(500)
                    tin = self.first(page, TOPIC_INPUT)
                    if not tin:
                        break
                    tin.fill(t)
                    page.wait_for_timeout(800)
                    sug = self.first(page, TOPIC_SUGGEST)
                    if sug:
                        sug.click()
                        chips_written = True
                        page.wait_for_timeout(400)
            except Exception:
                chips_written = False
            if not chips_written:
                try:
                    el = self.first(page, DESC_INPUT)
                    if el:
                        el.type(" #" + " #".join(
                            x.strip() for x in inp.topics.split(",") if x.strip()))
                except Exception:
                    pass

        # 展开「更多设置」，为 AI 声明 / 合集 / 定时做准备
        try:
            more = self.first(page, MORE_SETTINGS)
            if more:
                more.click()
                page.wait_for_timeout(800)
        except Exception:
            pass

        # AI 标识 [真机校验]：小红书要求对 AI 内容做声明，开关点一次即视为开启
        if inp.ai_disclosure:
            try:
                sw = self.first(page, AI_DISCLOSURE_SWITCH)
                if sw:
                    sw.click()
                    page.wait_for_timeout(400)
            except Exception:
                pass

        # 合集 [真机校验]：点合集入口后选择同名项
        if inp.collection:
            try:
                trigger = self.first(page, COLLECTION_TRIGGER)
                if trigger:
                    trigger.click()
                    page.wait_for_timeout(500)
                    opt = self.first(page, f'div:has-text("{inp.collection}")')
                    if opt:
                        opt.click()
            except Exception:
                pass

        # 定时发布：本项目由自带调度器到点执行，刻意不把时间再交给平台二次延迟。
        # 仅当调用方明确传入 scheduled_at（平台侧定时模式）时才尝试开启定时开关。
        if inp.scheduled_at:
            try:
                self.click(page, SCHEDULE_TOGGLE)
                page.wait_for_timeout(500)
            except Exception:
                pass

    def click_publish(self, page) -> None:
        self.click(page, PUBLISH_BUTTON)

    def verify(self, page) -> tuple[bool, str]:
        try:
            page.wait_for_function(
                f"document.body.innerText.includes('{SUCCESS_TEXT}')", timeout=60000
            )
            return True, "检测到“发布成功”文案，发布成功"
        except Exception:
            if "publish" not in page.url and ("notes" in page.url or "interaction" in page.url):
                return True, "已离开发布页，疑似发布成功"
            return False, "未检测到“发布成功”，发布结果待确认"

    # ---------- 发布后数据采集 ----------
    # 笔记管理页接口 /api/galaxy/v2/creator/note/user/posted 带 x-s/x-t 签名头，
    # 手动 fetch 会被拒（code=-1），所以改为监听页面自己发出的原生请求拿数据。
    # 返回 data.notes[]：id/display_title/view_count/likes/comments_count/
    # collected_count(收藏)/shared_count(分享)/time/type
    MANAGE_URL = "https://creator.xiaohongshu.com/new/note-manager"

    def collect_works(self, ctx) -> tuple[list[dict], str]:
        """读取「笔记管理」列表里每条笔记的阅读/赞/评/藏/分享。

        返回 (作品列表, 错误信息)，失败不抛异常。
        """
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        captured: list[dict] = []

        def on_response(resp):
            if "user/posted" in resp.url and resp.status == 200:
                try:
                    j = resp.json()
                    if j.get("code") == 0:
                        captured.append(j.get("data") or {})
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            page.goto(self.MANAGE_URL, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            return [], f"打开笔记管理页失败：{e}"
        page.wait_for_timeout(10000)
        page.remove_listener("response", on_response)
        if not captured:
            return [], "未捕获到笔记列表接口响应（页面结构可能已变）"

        works: list[dict] = []
        seen: set[str] = set()
        for data in captured:
            for n in data.get("notes") or []:
                nid = n.get("id") or ""
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                works.append({
                    "work_id": nid,
                    "work_url": (f"https://www.xiaohongshu.com/explore/{nid}"
                                 f"?xsec_token={n.get('xsec_token') or ''}"),
                    "work_title": (n.get("display_title") or "")[:80],
                    "description": (n.get("display_title") or "")[:200],
                    "published_at": n.get("time") or None,
                    "views": n.get("view_count") or 0,
                    "likes": n.get("likes") or 0,
                    "comments": n.get("comments_count") or 0,
                    "shares": n.get("shared_count") or 0,
                    "favorites": n.get("collected_count") or 0,
                })
        return works, ""

    # ---------- 评论采集 ----------
    # 个人创作者号在创作后台没有「评论管理」页，评论只在 www 笔记页。
    # 接口 GET edith.xiaohongshu.com/api/sns/web/v2/comment/page 带 x-s 签名，
    # 手动 fetch 会被拒，同样改为监听笔记页自己发出的原生请求。
    # 响应 data.comments[]：id/content/create_time(ms)/user_info.nickname/
    # sub_comments[]（楼中楼，挂在父评论下）；data.user_id 是登录者本人 id。
    @staticmethod
    def _fmt_ts(ts) -> str:
        try:
            from datetime import datetime
            return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    @staticmethod
    def _parse_comment_pages(datas: list[dict]) -> list[dict]:
        """把若干页 comment/page 响应解析成标准评论条目（含楼中楼）。"""
        items: list[dict] = []
        seen: set[str] = set()
        self_id = ""
        for d in datas:
            self_id = self_id or (d.get("user_id") or "")
            for c in d.get("comments") or []:
                cid = c.get("id") or ""
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                ui = c.get("user_info") or {}
                items.append({
                    "comment_id": cid, "parent_key": "",
                    "author": ui.get("nickname") or "观众",
                    "content": c.get("content") or "",
                    "created_text": XiaohongshuPublisher._fmt_ts(c.get("create_time")),
                    "is_author": bool(self_id) and ui.get("user_id") == self_id,
                })
                for sub in c.get("sub_comments") or []:
                    sid = sub.get("id") or ""
                    if not sid or sid in seen:
                        continue
                    seen.add(sid)
                    sui = sub.get("user_info") or {}
                    items.append({
                        "comment_id": sid, "parent_key": cid,
                        "author": sui.get("nickname") or "观众",
                        "content": sub.get("content") or "",
                        "created_text": XiaohongshuPublisher._fmt_ts(sub.get("create_time")),
                        "is_author": bool(self_id) and sui.get("user_id") == self_id,
                    })
        return items

    def collect_comments(self, ctx, works: list[dict]):
        """读有评论的笔记及其评论。返回 (按 work_id 分组结果, 错误信息)。"""
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # 先刷新作品列表：拿到最新的评论数和 xsec_token（评论页 URL 要带 token）
        fresh, werr = self.collect_works(ctx)
        if fresh:
            works = fresh
        if not works:
            return {}, werr or "没有可采集评论的作品"

        # 按 note_id 捕获各笔记的评论页响应
        pages_by_note: dict[str, list[dict]] = {}

        def on_response(resp):
            if "/api/sns/web/v2/comment/page" not in resp.url or resp.status != 200:
                return
            try:
                j = resp.json()
            except Exception:
                return
            if j.get("code") != 0:
                return
            from urllib.parse import urlparse, parse_qs
            nid = (parse_qs(urlparse(resp.url).query).get("note_id") or [""])[0]
            if nid:
                pages_by_note.setdefault(nid, []).append(j.get("data") or {})

        page.on("response", on_response)
        out: dict[str, tuple[dict, list[dict]]] = {}
        try:
            for w in works:
                nid = w.get("work_id") or ""
                if not nid or not (w.get("comments") or 0):
                    continue  # 评论数 0 的笔记跳过（数字是刚刷新的）
                url = w.get("work_url") or f"https://www.xiaohongshu.com/explore/{nid}"
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    continue
                page.wait_for_timeout(8000)
                # 滚动触发评论翻页，最多再翻 4 页
                for _ in range(4):
                    datas = pages_by_note.get(nid) or []
                    if datas and not datas[-1].get("has_more"):
                        break
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(2500)
                items = self._parse_comment_pages(pages_by_note.get(nid) or [])
                out[nid] = (w, items)
        finally:
            page.remove_listener("response", on_response)
        return out, ""

    # ---------- 一键回复 ----------
    # 评论接口带签名写不了，回复走 UI：打开笔记页 → 按作者+内容定位评论 →
    # 点它的「回复」→ 输入 → 点「发送」。整套动作已真机验证可行。
    def reply_comment(self, ctx, target: dict, text: str) -> tuple[bool, str]:
        """把 text 回复到 target（评论）下。返回 (是否成功, 错误信息)。"""
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        url = target.get("work_url") or \
            f"https://www.xiaohongshu.com/explore/{target.get('work_id')}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return False, "打开笔记页失败（www 登录态可能失效）"
        page.wait_for_timeout(8000)

        key = {"author": (target.get("author") or "")[:12],
               "content": (target.get("content") or "")[:15]}
        found = page.evaluate(
            """k => {
                 const items = [...document.querySelectorAll(
                   '[class*=comment-item], [class*=commentItem]')]
                   .filter(e => e.offsetHeight > 0);
                 const el = items.find(e => {
                   const t = e.innerText || '';
                   return (!k.author || t.includes(k.author))
                        && (!k.content || t.includes(k.content));
                 });
                 if (!el) return false;
                 const btn = [...el.querySelectorAll('span,div,a,button')]
                   .find(b => (b.innerText || '').trim() === '回复');
                 if (!btn) return false;
                 btn.click();
                 return true;
               }""", key)
        if not found:
            return False, "评论不在首屏，请到小红书 App 里回复"
        page.wait_for_timeout(2000)

        typed = page.evaluate(
            """text => {
                 const els = [...document.querySelectorAll(
                   'textarea,[contenteditable=true]')].filter(e => e.offsetHeight > 0);
                 if (!els.length) return false;
                 const el = els[els.length - 1];
                 el.focus();
                 if (el.isContentEditable) el.innerText = text; else el.value = text;
                 el.dispatchEvent(new Event('input', {bubbles: true}));
                 return true;
               }""", text)
        if not typed:
            return False, "找不到回复输入框"
        page.wait_for_timeout(1200)

        sent = page.evaluate(
            """() => {
                 const bs = [...document.querySelectorAll(
                   'button,[class*=submit],[class*=send],span,div')]
                   .filter(e => /^(发送|发布)$/.test((e.innerText||'').trim())
                          && e.offsetHeight > 0);
                 if (!bs.length) return false;
                 bs[0].click();
                 return true;
               }""")
        if not sent:
            return False, "找不到发送按钮"
        page.wait_for_timeout(3000)
        return True, ""

    # ---------- 私信采集 ----------
    # www 站「消息」页（/chat）会自动发出 /api/im/web/v3/chats（带 x-s 签名，
    # 手动 fetch 会被拒），翻页 page=0..N 直到取完。响应 data.chats[] 里
    # chat_user_id / info.nickname(或 user_name) / last_msg_content /
    # last_msg_time(ms) 齐全；未读数在 /api/im/web/chat/get_unread 的
    # user_chat_unread_counts{chat_user_id: n}；群聊在 /api/im/web/chats/group。
    CHAT_URL = "https://www.xiaohongshu.com/chat?channel_type=explore_feed"

    def collect_messages(self, ctx) -> tuple[list[dict], str]:
        """读私信会话的最近一条 + 未读数。返回 (标准条目列表, 错误信息)。"""
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        chat_pages: list[dict] = []
        group_pages: list[dict] = []
        unread_map: dict[str, int] = {}

        def on_response(resp):
            u = resp.url
            if resp.status != 200:
                return
            try:
                if "/im/web/v3/chats" in u:
                    j = resp.json()
                    if j.get("code") == 0 or j.get("success"):
                        chat_pages.append(j.get("data") or {})
                elif "/im/web/chats/group" in u:
                    j = resp.json()
                    if j.get("code") == 0 or j.get("success"):
                        group_pages.append(j.get("data") or {})
                elif "/im/web/chat/get_unread" in u:
                    j = resp.json()
                    counts = ((j.get("data") or {}).get("user_chat_unread_counts") or {})
                    for k, v in counts.items():
                        unread_map[str(k)] = int(v or 0)
            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(self.CHAT_URL, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            page.remove_listener("response", on_response)
            return [], f"打开私信页失败：{e}"
        page.wait_for_timeout(9000)
        page.remove_listener("response", on_response)

        if not chat_pages:
            return [], "未捕获到私信会话接口（登录态可能失效或页面结构已变）"

        # 同一接口会分页多次、字段形态略异：按会话 id 归并，保留信息更全的一条
        merged: dict[str, dict] = {}
        for data in chat_pages:
            for c in data.get("chats") or []:
                cid = str(c.get("chat_user_id") or c.get("user_id") or "")
                if not cid:
                    continue
                old = merged.get(cid)
                if old is None or (not old.get("last_msg_content")
                                   and c.get("last_msg_content")):
                    merged[cid] = c

        items: list[dict] = []
        for cid, c in merged.items():
            info = c.get("info") or {}
            sender = info.get("nickname") or info.get("user_name") or ""
            content = str(c.get("last_msg_content") or "")
            # 指纹带最后一条消息时间：同一会话来了新消息才会产生新行
            stamp = c.get("last_msg_time") or content[:30]
            items.append({
                "sender": sender,
                "content": content,
                "msg_key": f"{cid}:{stamp}",
                "unread_count": unread_map.get(cid, 0),
                "is_group": False,
                "created_text": self._fmt_ts(c.get("last_msg_time")),
            })
        for data in group_pages:
            for g in data.get("chats") or []:
                gid = str(g.get("chat_id") or g.get("group_id") or "")
                if not gid:
                    continue
                info = g.get("info") or {}
                items.append({
                    "sender": info.get("name") or info.get("nickname") or "群聊",
                    "content": str(g.get("last_msg_content") or ""),
                    "msg_key": "g" + gid,
                    "unread_count": 0,
                    "is_group": True,
                    "created_text": self._fmt_ts(g.get("last_msg_time")),
                })
        return items, ""
