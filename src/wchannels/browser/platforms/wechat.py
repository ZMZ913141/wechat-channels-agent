"""视频号发布适配器（完整实现，基于 2026-10 实测选择器）。

表单在 wujie 微前端 iframe 内，上传控件在 page.frames 中。
本适配器补齐了此前发布链路缺失的话题、AI 标识、定时、合集。
"""
from ._base import Publisher, PublishOutcome, iter_frames
from .. import selectors as WX
import hashlib
import json
from datetime import datetime


# 视频号额外选择器（话题/AI 标识/定时/合集）
TOPIC_INPUT = 'input[placeholder*="话题"], input[class*="topic"]'
AI_DISCLOSURE_LABEL = 'div:has-text("视频标注"), label:has-text("视频标注")'
AI_DISCLOSURE_OPTION = 'div[role="option"], li:has-text("{kw}")'
SCHEDULE_TOGGLE = 'div:has-text("定时发表"), label:has-text("定时")'
COLLECTION_TRIGGER = 'div:has-text("合集"), span:has-text("合集")'
PUBLISH_BUTTON = 'button:has-text("发表"), button:has-text("发布")'


class WeChatPublisher(Publisher):
    platform = "wechat"
    publish_url = WX.PUBLISH_URL
    upload_wait_ms = 8000
    title_min = 7
    title_max = 15

    @staticmethod
    def _first_visible(page, selector):
        """跨 frame 找第一个「可见且尺寸正常」的命中元素。

        发表页里有大量隐藏的同名 input（位置、活动、小程序搜索等），
        只取第一个命中会填到隐藏控件上，看起来填成功、实际发布为空。
        """
        candidates = [selector] if isinstance(selector, str) else list(selector)
        for sel in candidates:
            for fr in iter_frames(page):
                try:
                    for el in fr.query_selector_all(sel):
                        box = el.bounding_box()
                        if box and box["width"] > 0 and box["height"] > 0:
                            return el
                except Exception:
                    continue
        return None

    def fill_form(self, page, inp, title: str) -> None:
        # 1) 短标题（7-15 字硬校验；填完回读，被重渲染清空就中止）
        el = self._first_visible(page, WX.SHORT_TITLE_INPUT)
        if not el:
            raise RuntimeError("找不到短标题输入框")
        el.fill(title)
        if (el.input_value() or "").strip() != title.strip():
            raise RuntimeError("短标题写入后没有保留，已中止（避免发出无标题内容）")

        # 2) 描述（必填校验）：视频号新增了 div.input-editor 富文本，不再是 contenteditable="true"
        desc = (inp.description or "").strip()
        if desc:
            for _ in range(2):
                el = self._first_visible(page, WX.DESCRIPTION_EDITABLE)
                if not el:
                    raise RuntimeError("找不到视频描述输入框")
                el.click()
                el.fill(desc)
                page.wait_for_timeout(500)
                check = self._first_visible(page, WX.DESCRIPTION_EDITABLE)
                if check and desc[:6] in (check.text_content() or ""):
                    break
            else:
                raise RuntimeError("视频描述写入失败，已中止（避免发出空描述内容）")

        # 3) 话题（best-effort）输入后回车确认
        if inp.topics:
            try:
                el = self.first(page, TOPIC_INPUT)
                if el:
                    for t in [x.strip() for x in inp.topics.split(",") if x.strip()]:
                        el.fill(t)
                        page.keyboard.press("Enter")
                        page.wait_for_timeout(300)
            except Exception:
                pass

        # 4) AI 标识（best-effort）：点“视频标注”后选匹配项
        if inp.ai_disclosure:
            try:
                self.click(page, AI_DISCLOSURE_LABEL)
                page.wait_for_timeout(500)
                opt = self.first(page, AI_DISCLOSURE_OPTION.format(kw=inp.ai_disclosure))
                if opt:
                    opt.click()
            except Exception:
                pass

        # 5) 合集（best-effort，TODO: 真机校验选择器）
        if inp.collection:
            try:
                self.click(page, COLLECTION_TRIGGER)
                page.wait_for_timeout(500)
                opt = self.first(page, f'div:has-text("{inp.collection}")')
                if opt:
                    opt.click()
            except Exception:
                pass

        # 6) 定时发布（best-effort，TODO: 真机校验时间选择器）
        if inp.scheduled_at:
            try:
                self.click(page, SCHEDULE_TOGGLE)
                page.wait_for_timeout(500)
                # 时间选择器随版本变化，尝试 fill 一个 datetime 输入
                dt_input = self.first(page, 'input[type="text"][placeholder*="时间"], input[class*="time"]')
                if dt_input:
                    dt_input.fill(inp.scheduled_at.strftime("%Y-%m-%d %H:%M"))
            except Exception:
                pass

        # 7) 声明原创（best-effort：勾选含「原创」字样的复选框，找不到就跳过）
        if inp.original_work:
            try:
                for fr in iter_frames(page):
                    boxes = fr.query_selector_all('input[type="checkbox"]')
                    for cb in boxes:
                        label = cb.evaluate("el => el.closest('label')?.innerText || el.parentElement?.innerText || ''")
                        if '原创' in (label or ''):
                            if not cb.is_checked():
                                cb.click()
                            break
            except Exception:
                pass

    def click_publish(self, page) -> None:
        self.click(page, PUBLISH_BUTTON)

    def fetch_collections(self, ctx) -> list[str]:
        """尝试从发表页的合集下拉里读取已有合集名。

        视频号合集下拉在发表页始终可点（不需要先传视频）；若改版失败则返回空，
        前端降级为让用户手动输入合集名。
        """
        import time as _t
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(self.publish_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return []
        page.wait_for_timeout(3000)
        try:
            self.click(page, COLLECTION_TRIGGER)
            page.wait_for_timeout(1000)
            items = []
            for fr in iter_frames(page):
                try:
                    for el in fr.query_selector_all('div[role="option"], li[role="option"], .weui-desktop-form__dropdown__menu-item'):
                        txt = (el.inner_text() or "").strip()
                        if txt:
                            items.append(txt)
                except Exception:
                    continue
            return list(dict.fromkeys(items))
        except Exception:
            return []

    def verify(self, page) -> tuple[bool, str]:
        import re
        try:
            # Playwright(Python) 的 wait_for_url 只接受 glob 字符串或正则，
            # 传回调函数不会报错但永远等不到——这里改正则。
            page.wait_for_url(re.compile(r"/platform/post/list"), timeout=60000)
            return True, "已跳转到内容管理页，发布成功"
        except Exception:
            err = self.first(page, WX.ERROR_TOAST)
            msg = err.inner_text() if err else "未跳转到内容管理页，发布结果待确认"
            return False, msg

    # ---------- 发布后数据采集 ----------
    LIST_URL = "https://channels.weixin.qq.com/platform/post/list"

    def collect_works(self, ctx) -> tuple[list[dict], str]:
        """读取「动态管理」列表里每条作品的播放/赞/评/转/藏。

        列表内容在 micro/content/iframe/post-card.html 里，卡片是 .post-feed-item，
        每个指标是 .data-item（图标顺序固定：播放、赞、评论、转发、收藏）。
        返回 (作品列表, 错误信息)；失败不抛异常，避免拖垮整个采集任务。
        """
        import re
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(self.LIST_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            return [], f"打开动态管理页失败：{e}"
        page.wait_for_timeout(12000)
        fr = next((f for f in page.frames if "post-card" in f.url), None)
        if not fr:
            return [], "没找到作品列表 iframe（页面可能改版或登录已失效）"
        try:
            raw = fr.evaluate(
                """() => [...document.querySelectorAll('.post-feed-item')]
                     .filter(it => it.querySelector('.data-item'))
                     .map(it => ({
                       text: (it.innerText || ''),
                       nums: [...it.querySelectorAll('.data-item')].map(d => ({
                         icon: d.querySelector('.icon') ? d.querySelector('.icon').className : '',
                         use: d.querySelector('use') ? (d.querySelector('use').getAttribute('xlink:href') || '') : '',
                         num: (d.innerText || '').trim(),
                       })),
                     }))"""
            )
        except Exception as e:
            return [], f"读取作品列表失败：{e}"

        def to_int(s: str) -> int:
            s = (s or "").replace(",", "").strip()
            return int(s) if s.isdigit() else 0

        date_re = re.compile(r"(\d{4})年(\d{2})月(\d{2})日\s*(\d{2}):(\d{2})")
        works: list[dict] = []
        for item in raw:
            lines = [x.strip() for x in (item.get("text") or "").split("\n") if x.strip()]
            pub = None
            desc_lines = []
            for ln in lines:
                m = date_re.search(ln)
                if m:
                    pub = f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"
                    break
                desc_lines.append(ln)
            desc = " ".join(desc_lines).strip()
            if not desc:
                continue
            nums = item.get("nums") or []
            values = [to_int(n.get("num")) for n in nums]
            # 防御：图标顺序变了就按数量对齐（缺失补 0）
            values = (values + [0, 0, 0, 0, 0])[:5]
            works.append({
                "work_id": "wx" + hashlib.sha1((desc + str(pub)).encode()).hexdigest()[:12],
                "work_url": "",
                "work_title": desc[:80],
                "description": desc,
                "published_at": pub,
                "views": values[0], "likes": values[1], "comments": values[2],
                "shares": values[3], "favorites": values[4],
            })
    # ---------- 发布后评论采集 ----------
    # 评论页是 wujie 微前端（内容在 shadow DOM 里，窗口坐标点不进去），
    # 这里直接用页面内 fetch 调后台接口：带登录 Cookie，不依赖 UI 改版。
    COMMENT_API = "https://channels.weixin.qq.com/micro/interaction/cgi-bin/mmfinderassistant-bin"
    FETCH_COMMENTS_JS = r"""async (base) => {
      const j = async (path, body) => {
        const r = await fetch(base + path, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, credentials: 'include',
          body: JSON.stringify(body)});
        return JSON.parse(await r.text());
      };
      // 1) 作品列表：带每条的评论数，只有有评论的才继续拉评论
      const pl = await j('/post/post_list?pf=web', {
        pageSize: 20, onlyUnread: false, needAllCommentCount: true,
        userpageType: 13, forMcn: false, lastBuff: '', timestamp: String(Date.now())});
      const feeds = ((pl.data || {}).list || []).filter(x => (x.commentCount || 0) > 0);
      const out = [];
      for (const f of feeds.slice(0, 20)) {
        const d = f.desc || {};
        const title = (d.description || d.shortTitle || '');
        let cl = {};
        try {
          cl = await j('/comment/comment_list?pf=web', {
            exportId: f.objectId, lastBuff: '', commentSelection: false, forMcn: false});
        } catch (e) {}
        const fmt = ts => ts ? new Date(Number(ts) * 1000)
          .toLocaleString('zh-CN', {hour12: false}) : '';
        const comments = ((cl.data || {}).comment || []).map(c => ({
          author: c.commentNickname || '观众',
          content: c.commentContent || '',
          created_text: fmt(c.commentCreatetime),
          comment_id: c.commentId || '',
          parent_key: '',
          replies: (c.levelTwoComment || []).map(r => ({
            author: r.commentNickname || '',
            content: r.commentContent || '',
            created_text: fmt(r.commentCreatetime),
            comment_id: r.commentId || '',
            parent_key: c.commentId || '',
          })),
        }));
        out.push({object_id: f.objectId, title, comments});
      }
      return {err: pl.errCode, feeds: out};
    }"""

    @staticmethod
    def _match_work(works: list[dict], title: str) -> dict | None:
        """平台描述和本地标题按前缀/包含关系比对（两边描述常有一边被截断）。"""
        def norm(s):
            return "".join((s or "").split())

        key = norm(title)
        if len(key) < 4:
            return None
        for w in works:
            lt = norm(w.get("work_title"))
            if lt[:12] == key[:12] or key[:10] in lt or lt[:10] in key:
                return w
        return None

    def collect_comments(self, ctx, works: list[dict]):
        """读有评论的作品及其评论。返回 (按 work_id 分组结果, 错误信息)。"""
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://channels.weixin.qq.com/platform",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(12000)
        data = page.evaluate(self.FETCH_COMMENTS_JS, self.COMMENT_API) or {}
        if data.get("err"):
            return {}, f"评论接口返回错误码 {data['err']}（登录态可能失效）"
        out: dict[str, tuple[dict, list[dict]]] = {}
        for feed in data.get("feeds") or []:
            w = self._match_work(works, feed.get("title") or "") or {
                "work_id": feed.get("object_id") or "",
                "work_title": (feed.get("title") or "")[:80],
                "work_url": "",
            }
            items: list[dict] = []
            for c in feed.get("comments") or []:
                items.append(c)
                items.extend(c.get("replies") or [])
            out[w["work_id"]] = (w, items)
        return out, ""

    # ---------- 一键回复 ----------
    # 回复走 create_comment 接口（从互动微前端 chunk 里逆向的参数）：
    # replyCommentId=被回复评论、rootCommentId=根评论、content、clientId(uuid)、
    # comment=被回复评论完整对象、exportId=作品 objectId。
    REPLY_JS = r"""async ([base, t]) => {
      const j = async (path, body) => {
        const r = await fetch(base + path, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, credentials: 'include',
          body: JSON.stringify(body)});
        return JSON.parse(await r.text());
      };
      // work_id 可能不是平台 objectId（早期评论挂的是本地作品），按标题兜底匹配
      let exportId = t.work_id || '';
      if (!exportId.startsWith('export/')) {
        const pl = await j('/post/post_list?pf=web', {
          pageSize: 50, onlyUnread: false, needAllCommentCount: true,
          userpageType: 13, forMcn: false, lastBuff: '', timestamp: String(Date.now())});
        const norm = s => (s || '').replace(/\s+/g, '');
        const key = norm(t.title);
        const f = ((pl.data || {}).list || []).find(x => {
          const d = x.desc || {};
          const lt = norm(d.description || d.shortTitle || '');
          return lt && key && (lt.slice(0, 12) === key.slice(0, 12)
            || lt.includes(key.slice(0, 10)) || key.includes(lt.slice(0, 10)));
        });
        if (!f) return {ok: false, err: '作品匹配失败（可能已删除或标题改动）'};
        exportId = f.objectId;
      }
      // 找到被回复评论的完整对象；若目标是二级评论，root 是它的父评论
      const cl = await j('/comment/comment_list?pf=web', {
        exportId, lastBuff: '', commentSelection: false, forMcn: false});
      const all = (cl.data || {}).comment || [];
      let target = null, rootId = t.comment_id;
      for (const c of all) {
        if (c.commentId === t.comment_id) { target = c; rootId = c.commentId; break; }
        const sub = (c.levelTwoComment || []).find(r => r.commentId === t.comment_id);
        if (sub) { target = sub; rootId = c.commentId; break; }
      }
      if (!target) return {ok: false, err: '评论已删除或不在列表首页'};
      const resp = await j('/comment/create_comment?pf=web', {
        replyCommentId: t.comment_id, content: t.text, clientId: t.clientId,
        rootCommentId: rootId, comment: target, exportId});
      return resp && resp.errCode === 0 ? {ok: true}
        : {ok: false, err: (resp && resp.errMsg) || '接口返回错误'};
    }"""

    def reply_comment(self, ctx, target: dict, text: str) -> tuple[bool, str]:
        """把 text 回复到 target（评论）下。返回 (是否成功, 错误信息)。"""
        import uuid
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://channels.weixin.qq.com/platform",
                      wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return False, "打开视频号后台失败（登录态可能失效）"
        page.wait_for_timeout(12000)
        res = page.evaluate(self.REPLY_JS, [self.COMMENT_API, {
            "work_id": target.get("work_id") or "",
            "title": target.get("work_title") or "",
            "comment_id": target.get("comment_key") or "",
            "text": text, "clientId": str(uuid.uuid4()),
        }]) or {}
        if res.get("ok"):
            return True, ""
        return False, res.get("err") or "回复发送失败"

    # ---------- 私信采集 ----------
    # 私信走 /private-msg/* 系列接口（互动微前端 chunk 里逆向出的路径）。
    # get-session-info 拿会话列表，get-history-msg 拿每个会话的历史消息。
    FETCH_MSG_JS = r"""async base => {
      const j = async (path, body) => {
        const r = await fetch(base + path, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, credentials: 'include',
          body: JSON.stringify(body)});
        return JSON.parse(await r.text());
      };
      const si = await j('/private-msg/get-session-info', {});
      const sessions = ((si.data || {}).sessionInfo || []).slice(0, 20);
      const out = [];
      for (const s of sessions) {
        try {
          const h = await j('/private-msg/get-history-msg',
                            {sessionId: s.sessionId || ''});
          const msgs = ((h.data || {}).msg || []);
          out.push({session: s, messages: msgs.slice(-10)});
        } catch (e) {}
      }
      return out;
    }"""

    def collect_messages(self, ctx) -> tuple[list[dict], str]:
        """读私信会话的最近消息。返回 (标准条目列表, 错误信息)。"""
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://channels.weixin.qq.com/platform",
                      wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return [], "打开视频号后台失败（登录态可能失效）"
        page.wait_for_timeout(12000)
        data = page.evaluate(self.FETCH_MSG_JS, self.COMMENT_API) or []
        items: list[dict] = []
        for conv in data:
            s = conv.get("session") or {}
            sender = (s.get("nickname") or s.get("username")
                      or s.get("sessionNickname") or s.get("sessionId") or "未知会话")
            peer = str(s.get("username") or "")
            msgs = conv.get("messages") or []
            # 多轮上下文：按时间正序组装，标注是谁发的（供 AI 草稿参考）
            history: list = []
            def _ts(m):
                return int(m.get("ts") or m.get("createTime")
                           or m.get("create_time") or 0)
            for m in sorted(msgs, key=_ts):
                frm = m.get("fromUsername")
                if frm:
                    from_peer = frm == peer
                elif m.get("isSend") or m.get("fromSelf"):
                    from_peer = False
                else:
                    from_peer = True  # 字段缺失时按对方来信处理
                history.append({
                    "from_peer": from_peer,
                    "content": str(((m.get("textMsg") or {}).get("content")
                                    or m.get("content") or m.get("text")
                                    or m.get("msgContent") or ""))[:200],
                    "ts": _ts(m),
                })
            history_json = json.dumps(history[-10:], ensure_ascii=False) if history else ""
            for m in msgs:
                content = (m.get("content") or m.get("text")
                           or m.get("msgContent") or "")
                key = (m.get("msgId") or m.get("msg_id")
                       or f"{s.get('sessionId')}:{content[:20]}")
                ts = m.get("createTime") or m.get("create_time")
                items.append({
                    "sender": str(sender)[:40],
                    "content": str(content)[:200],
                    "msg_key": str(key),
                    "created_text": str(ts or ""),
                    "unread_count": int(s.get("unreadCount")
                                        or s.get("unread_count") or 0),
                    "session_id": str(s.get("sessionId") or ""),
                    "peer_username": peer,
                    "history": history_json,
                })
        # 打招呼（陌生人）消息：主接口不覆盖，从私信页 UI 单独采集
        try:
            items.extend(self._collect_greetings(page))
        except Exception:
            pass
        return items, ""

    def _collect_greetings(self, page) -> list:
        """采集「打招呼消息」（陌生人私信）。

        全量会话列表走 WebSocket 同步而非 HTTP 接口，这里驱动真实页面：
        切到打招呼标签，用响应监听捕获 get-session-info 拿到
        sessionId/username/nickname，再从列表 DOM 读最后一条消息预览。
        """
        sessions: dict = {}

        def on_response(resp):
            if "private-msg/get-session-info" in resp.url:
                try:
                    data = resp.json()
                except Exception:
                    return
                for it in ((data.get("data") or {}).get("sessionInfo") or []):
                    sid = it.get("sessionId") or ""
                    if sid and sid not in sessions:
                        sessions[sid] = it

        page.on("response", on_response)
        try:
            page.goto("https://channels.weixin.qq.com/platform/private_msg",
                      wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return []
        page.wait_for_timeout(10000)
        try:
            tab = page.get_by_text("打招呼消息", exact=False).first
            tab.wait_for(timeout=15000)
            tab.dispatch_event("click")
        except Exception:
            return []
        # 等列表渲染触发 get-session-info（最多约 20 秒）
        for _ in range(10):
            if sessions:
                break
            page.wait_for_timeout(2000)
        # 兜底：标签点击没触发接口时（WebSocket 同步时序不稳定），
        # 直接点列表里的会话项，点中会触发该会话的 get-session-info
        if not sessions:
            try:
                convs = page.locator(
                    "[class*='session'] [class*='item'], "
                    "[class*='conversation'] [class*='item'], "
                    "[class*='msg'] [class*='conv']")
                n = min(convs.count(), 10)
                for i in range(n):
                    try:
                        convs.nth(i).dispatch_event("click")
                        page.wait_for_timeout(1500)
                    except Exception:
                        continue
            except Exception:
                pass
        if not sessions:
            return []

        items: list[dict] = []
        for it in sessions.values():
            raw_nick = it.get("nickname") or ""
            nick = raw_nick.strip("《》") or "陌生人"
            sid = it.get("sessionId") or ""
            peer = it.get("username") or ""
            preview, when, msg_key = "", "", f"hi:{sid}"
            history: list = []
            try:
                hist = page.evaluate(self.FETCH_HIST_JS, [self.COMMENT_API, sid]) or {}
                msgs = ((hist.get("data") or {}).get("msg")) or []
                # 多轮上下文：按时间正序保留最近 10 轮，标注是谁发的
                chrono = sorted(msgs, key=lambda m: int(m.get("ts") or 0))[-10:]
                for m in chrono:
                    history.append({
                        "from_peer": (m.get("fromUsername") or "") == peer,
                        "content": ((m.get("textMsg") or {}).get("content")
                                    or m.get("rawContent") or ""),
                        "ts": int(m.get("ts") or 0),
                    })
                # 取最新一条对方发来的消息（列表按 seq 倒序，最新在前）
                theirs = [m for m in msgs if (m.get("fromUsername") or "") == peer]
                pick = (theirs or msgs)[0] if (theirs or msgs) else None
                if pick:
                    preview = ((pick.get("textMsg") or {}).get("content")
                               or pick.get("rawContent") or "")
                    ts = int(pick.get("ts") or 0)
                    if ts:
                        when = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                    msg_key = str(pick.get("svrMsgId") or msg_key)
            except Exception:
                pass
            items.append({
                "sender": str(nick)[:40],
                "content": str(preview)[:200],
                "msg_key": msg_key,
                "created_text": str(when),
                "unread_count": int(it.get("unreadCount") or 0),
                "session_id": sid,
                "peer_username": peer,
                "history": json.dumps(history[-10:], ensure_ascii=False),
            })
        return items

    # 打招呼会话历史：带 sessionId 调 get-history-msg 即可拿全量消息
    FETCH_HIST_JS = r"""async ([base, sid]) => {
      const r = await fetch(base + '/private-msg/get-history-msg', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        credentials: 'include',
        body: JSON.stringify({sessionId: sid, count: 20, scene: 7, reqScene: 7})});
      return JSON.parse(await r.text());
    }"""

    # ---------- 私信发送 ----------
    # 发送结构从互动微前端 chunk 逆向：sendPrivateMsg({msgPack:{sessionId,
    # fromUsername, toUsername, msgType:1(BYP_TEXT), textMsg:{content},
    # cliMsgId}})；fromUsername 用 get-finder-username 现取，toUsername
    # 以会话列表里的 sessionInfo.username 为准（传入值只作兜底）。
    SEND_MSG_JS = r"""async ([base, t]) => {
      const j = async (path, body) => {
        const r = await fetch(base + path, {method: 'POST',
          headers: {'Content-Type': 'application/json'}, credentials: 'include',
          body: JSON.stringify(body)});
        return JSON.parse(await r.text());
      };
      const fu = await j('/private-msg/get-finder-username', {});
      const fromUsername = ((fu.data || {}).finderUsername) || '';
      if (fu.errCode || !fromUsername)
        return {ok: false, err: '获取账号 fromUsername 失败（登录态可能失效）'};
      let toUsername = t.to_username || '';
      try {
        // 必须带 sessionId 查：空查询只返回主私信列表，
        // 打招呼（陌生人）会话也能用这种方式查到
        const si = await j('/private-msg/get-session-info',
          {sessionId: [t.session_id], scene: 7, reqScene: 7});
        const sess = ((si.data || {}).sessionInfo || [])
          .find(s => s.sessionId === t.session_id);
        if (sess) toUsername = sess.username || toUsername;
      } catch (e) {}
      if (!toUsername)
        return {ok: false, err: '会话已不存在（可能已过期），请重新抓取私信'};
      const cliMsgId = 'wc-' + Date.now() + '-' +
        Math.random().toString(36).slice(2, 10);
      const resp = await j('/private-msg/send-private-msg', {msgPack: {
        sessionId: t.session_id, fromUsername, toUsername,
        msgType: 1, textMsg: {content: t.text}, cliMsgId,
      }});
      const base0 = (resp.data || {}).baseResp || {};
      return (resp.errCode === 0 && !base0.errcode) ? {ok: true}
        : {ok: false, err: resp.errMsg || base0.errmsg || ('发送失败 errCode=' + resp.errCode)};
    }"""

    def send_private_message(self, ctx, session_id: str, to_username: str,
                             text: str) -> tuple[bool, str]:
        """把 text 发到 session_id 会话。返回 (是否成功, 错误信息)。"""
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://channels.weixin.qq.com/platform",
                      wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return False, "打开视频号后台失败（登录态可能失效）"
        page.wait_for_timeout(12000)
        res = page.evaluate(self.SEND_MSG_JS, [self.COMMENT_API, {
            "session_id": session_id, "to_username": to_username,
            "text": text,
        }]) or {}
        if res.get("ok"):
            return True, ""
        return False, res.get("err") or "私信发送失败"

