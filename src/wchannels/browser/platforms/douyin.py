"""抖音发布适配器（选择器基于公开调研，抖音页面改版频繁，需真实账号真机校验）。

流程：creator.douyin.com 落地页 → 点「发布视频」进入发布表单 → 上传 →
标题(≤30 字) / 简介 / 话题(回车确认) / 更多设置(AI 标识、定时、合集)。
成功判定：出现「发布成功」文案。

字段完整性：上传 / 标题 / 描述 / 话题 / 封面(基类通用) 已实现；
AI 标识 / 定时 / 合集为 best-effort 骨架，选择器标注 [真机校验]。
"""
from ._base import Publisher, PublishOutcome, find_file_input

# 直接进发布表单页；落地页首页会随版本变成活动页，点「发布视频」容易点错入口
UPLOAD_ENTRY = "https://creator.douyin.com/creator-micro/content/upload"
ENTER_PUBLISH = ('button:has-text("发布视频"), div:has-text("发布视频"), '
                 'span:has-text("发布视频")')
PUBLISH_BUTTON = 'button:has-text("发布"), button:has-text("立即发布")'
SUCCESS_TEXT = "发布成功"
TITLE_INPUT = 'input[placeholder*="标题"], textarea[placeholder*="标题"]'
TOPIC_INPUT = 'input[placeholder*="话题"], input[placeholder*="添加话题"]'
DESC_INPUT = 'textarea[placeholder*="简介"], div[contenteditable="true"]'
# [真机校验] 以下为「更多设置」区骨架，DOM 随版本变化，均 best-effort
MORE_SETTINGS = 'div:has-text("更多设置"), button:has-text("展开")'
AI_DISCLOSURE_SWITCH = 'div:has-text("AI生成"), input[type="checkbox"], [role="switch"]'
SCHEDULE_TOGGLE = 'div:has-text("定时发布"), label:has-text("定时发布")'
SCHEDULE_INPUT = 'input[placeholder*="选择日期"], input[placeholder*="时间"]'
COLLECTION_TRIGGER = 'div:has-text("合集"), span:has-text("合集")'


class DouyinPublisher(Publisher):
    platform = "douyin"
    publish_url = UPLOAD_ENTRY
    upload_wait_ms = 8000
    title_min = 0
    title_max = 30

    def enter_publish_page(self, page) -> None:
        # 已是发布表单页就直接用；被重定向到首页时才点「发布视频」
        if find_file_input(page):
            return
        btn = self.first(page, ENTER_PUBLISH)
        if btn:
            btn.click()
            page.wait_for_timeout(3000)

    def fill_form(self, page, inp, title: str) -> None:
        self._expected_title = title
        # 标题
        try:
            self.fill_text(page, TITLE_INPUT, title)
        except Exception:
            pass

        # 话题（回车确认）
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

        # 描述（best-effort）
        try:
            el = self.first(page, DESC_INPUT)
            if el:
                el.click()
                el.fill(inp.description or "")
        except Exception:
            pass

        # 展开「更多设置」，为 AI 标识 / 定时 / 合集做准备
        try:
            more = self.first(page, MORE_SETTINGS)
            if more:
                more.click()
                page.wait_for_timeout(800)
        except Exception:
            pass

        # AI 标识 [真机校验]：抖音为「内容由 AI 生成」声明开关，点一次即视为开启
        if inp.ai_disclosure:
            try:
                sw = self.first(page, AI_DISCLOSURE_SWITCH)
                if sw:
                    sw.click()
                    page.wait_for_timeout(400)
            except Exception:
                pass

        # 合集 [真机校验]：点合集后选择同名项
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
        # 仅当调用方明确传入 scheduled_at（平台侧定时模式）时才尝试填写。
        if inp.scheduled_at:
            try:
                self.click(page, SCHEDULE_TOGGLE)
                page.wait_for_timeout(500)
                dt_input = self.first(page, SCHEDULE_INPUT)
                if dt_input:
                    dt_input.fill(inp.scheduled_at.strftime("%Y-%m-%d %H:%M"))
            except Exception:
                pass

    def click_publish(self, page) -> None:
        # 注意不能用 has-text("发布")：左侧导航「作品发布」按钮也含「发布」，
        # 点它会重载页面（实测导致只留下草稿、什么都没发出去）。
        # 这里要求按钮文案精确等于「发布」/「立即发布」。
        for sel in ('button:text-is("发布")', 'button:text-is("立即发布")'):
            btn = self.first(page, sel)
            if btn:
                btn.click()
                return
        raise RuntimeError("找不到发布提交按钮（可能页面改版）")

    def verify(self, page) -> tuple[bool, str]:
        import time
        # 成功有两种表现：浮层「发布成功」文案，或跳转到作品管理页后上传真正完成。
        # 抖音会先跳转管理页，再在后台继续上传（「作品上传中，请勿关闭页面 / 上传完成后
        # 将自动发布 49%」）。旧逻辑把跳转当成功、立刻关浏览器，上传被中断只留草稿。
        # 所以这里轮询等待：必须看到标题出现在列表，或上传提示出现后消失，才算完成。
        # 页面跳转瞬间会抛 “execution context destroyed”，重试即可，不能直接判失败。
        expect = (getattr(self, "_expected_title", "") or "").strip()[:10]
        end = time.time() + 900
        marker_seen = False
        manage_at = None
        qr_opened = False
        while time.time() < end:
            try:
                url = page.url or ""
                body = page.inner_text("body")[:1500]
            except Exception:
                page.wait_for_timeout(1500)
                continue
            if "发布成功" in body:
                return True, "检测到“发布成功”文案，发布成功"
            if ("接收短信验证码" in body or "获取验证码" in body
                    or "使用原设备扫码" in body):
                # 发布验证弹窗默认是短信；自动切到「使用原设备扫码」，
                # 用户用登录该号的手机扫一下即可继续，不用等短信
                if not qr_opened:
                    try:
                        link = self.first(page, 'text=使用原设备扫码')
                        if link:
                            link.click()
                            qr_opened = True
                            page.wait_for_timeout(2500)
                            page.bring_to_front()
                    except Exception:
                        pass
                page.wait_for_timeout(3000)  # 等用户扫码确认
                continue
            if "上传失败" in body or "发布失败" in body:
                return False, "平台提示上传/发布失败，请查看浏览器页面"
            if "/content/manage" in url:
                if manage_at is None:
                    manage_at = time.time()
                if "作品上传中" in body or "上传完成后将自动发布" in body:
                    marker_seen = True
                elif marker_seen or (expect and expect in body):
                    return True, "作品上传完成，已发布到作品管理页"
            page.wait_for_timeout(2000)
        try:
            body = page.inner_text("body")[:600]
        except Exception:
            body = ""
        if "作品上传中" in body or "上传完成后将自动发布" in body:
            return False, "作品仍在后台上传，未确认完成（浏览器需保持打开）"
        if "接收短信验证码" in body or "获取验证码" in body or "扫码" in body:
            return False, "发布需要扫码/短信验证，等待超时，本次未提交成功"
        return False, "未检测到“发布成功”，发布结果待确认"

    # ---------- 发布后数据采集 ----------
    MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"

    def collect_works(self, ctx) -> tuple[list[dict], str]:
        """读取「作品管理」列表里每条作品的播放/赞/评/分享。

        抖音列表卡片没有稳定 class（改版频繁），这里走「文案标签」解析：
        找到同时含「播放」「点赞」的最小块，再按标签取数，抗 class 改名。
        返回 (作品列表, 错误信息)，失败不抛异常。
        """
        import hashlib
        import re
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(self.MANAGE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            return [], f"打开作品管理页失败：{e}"
        page.wait_for_timeout(12000)
        try:
            raw = page.evaluate(
                """() => {
                    const hit = e => /播放/.test(e.innerText||'') && /点赞/.test(e.innerText||'');
                    const all = [...document.querySelectorAll('div,li,section')].filter(hit);
                    // 取「最小」的命中块：内部没有别的命中块，避免把整页当成一个作品
                    const leaves = all.filter(e => ![...e.querySelectorAll('div,li,section')].some(hit));
                    // 指标块不含标题，往上找到整张作品卡片（靠操作按钮识别），
                    // 标题/日期/操作都在卡片里；class 带 video-card 的可能是指标容器本身
                    return leaves.slice(0, 30).map(e => {
                        let card = e;
                        for (let i = 0; i < 5 && card; i++) {
                            const t = card.innerText || '';
                            if (/编辑作品|设置权限|删除作品/.test(t)) break;
                            card = card.parentElement;
                        }
                        return (card || e).innerText || '';
                    });
                }"""
            )
        except Exception as e:
            return [], f"读取作品列表失败：{e}"

        def num(txt: str) -> int:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(万|w|W)?", txt or "")
            if not m:
                return 0
            v = float(m.group(1))
            if m.group(2):
                v *= 10000
            return int(v)

        def pick(text: str, *labels: str) -> int:
            for lb in labels:
                m = re.search(lb + r"\s*(\d+(?:\.\d+)?)\s*(万|w|W)?", text)
                if m:
                    return num(m.group(0).replace(lb, "").strip())
            return 0

        # 卡片整块的文本里混着操作按钮、日期和指标，先剔掉这些行再取标题
        noise = {"置顶", "编辑作品", "设置权限", "取消置顶", "分享", "删除作品",
                 "流量减少", "查看详情", "播放", "点赞", "评论", "收藏", "完播率",
                 "2秒跳出率", "吸粉量", "弹幕", "封面点击率", "平均播放占比"}

        def pick_title(lines: list[str]) -> str:
            for ln in lines:
                if ln in noise or re.fullmatch(r"[\d.%:：万wW\-+ ]+", ln):
                    continue
                if re.search(r"20\d{2}年\d{1,2}月\d{1,2}日", ln):
                    continue
                return ln
            return lines[0] if lines else ""

        works: list[dict] = []
        for text in raw:
            lines = [x.strip() for x in (text or "").split("\n") if x.strip()]
            if not lines:
                continue
            title = pick_title(lines)
            m = re.search(r"(20\d{2}[年\-/.]\d{1,2}[月\-/.]\d{1,2}日?)", text)
            pub = m.group(1) if m else None
            if pub:
                pub = re.sub(r"[年月/.]", "-", pub).replace("日", "")
            works.append({
                "work_id": "dy" + hashlib.sha1((title + str(pub)).encode()).hexdigest()[:12],
                "work_url": "",
                "work_title": title[:80],
                "description": title[:200],
                "published_at": pub,
                "views": pick(text, "播放"),
                "likes": pick(text, "点赞"),
                "comments": pick(text, "评论"),
                "shares": pick(text, "分享"),
                "favorites": pick(text, "收藏"),
            })
        return works, ""

    # ---------- 发布后评论采集 ----------
    # 评论页是异步渲染的单页应用，直接调后台接口更稳：
    # 作品列表 /aweme/v1/creator/item/list ，评论 /aweme/v1/creator/comment/list
    # 实测这两条接口只要带登录 Cookie 即可，无需签名参数。
    COMMENT_PAGE = "https://creator.douyin.com/creator-micro/interactive/comment"
    FETCH_COMMENTS_JS = r"""async () => {
      const get = async u => {
        const r = await fetch(u, {credentials: 'include'});
        return await r.json();
      };
      // 1) 作品列表（最多翻 3 页）
      let cursor = '0', items = [];
      for (let p = 0; p < 3; p++) {
        const j = await get('/aweme/v1/creator/item/list/?cursor=' + cursor
                            + '&count=20&aid=2906');
        if (j.status_code !== 0) return {err: j.status_code || -1};
        items = items.concat(j.item_info_list || []);
        if (!j.has_more || !j.cursor) break;
        cursor = String(j.cursor);
      }
      // 2) 只拉有评论的作品
      const fmt = ts => ts ? new Date(Number(ts) * 1000)
        .toLocaleString('zh-CN', {hour12: false}) : '';
      const out = [];
      for (const it of items.filter(x => (x.comment_count || 0) > 0).slice(0, 20)) {
        let cl = {};
        try {
          cl = await get('/aweme/v1/creator/comment/list/?cursor=0&count=20&sort=TIME'
                         + '&aid=2906&item_id=' + encodeURIComponent(it.item_id));
        } catch (e) {}
        // 一级评论 + 每条的二级回复（回复单独拉接口）
        const comments = [];
        for (const c of (cl.comment_info_list || [])) {
          comments.push({
            author: (c.user_info || {}).screen_name || '观众',
            content: c.text || '',
            created_text: fmt(c.create_time),
            comment_id: c.comment_id || '',
            like_count: Number(c.digg_count || 0),
            is_author: !!c.is_author,
            parent_key: '',
          });
          if (Number(c.reply_count || 0) > 0 && c.comment_id) {
            let rl = {};
            try {
              rl = await get('/aweme/v1/creator/comment/reply/list?cursor=0&count=20'
                             + '&sort=TIME&comment_id=' + encodeURIComponent(c.comment_id));
            } catch (e) {}
            for (const r of (rl.comment_info_list || [])) {
              comments.push({
                author: (r.user_info || {}).screen_name || '观众',
                content: r.text || '',
                created_text: fmt(r.create_time),
                comment_id: r.comment_id || '',
                like_count: Number(r.digg_count || 0),
                is_author: !!r.is_author,
                parent_key: c.comment_id,
              });
            }
          }
        }
        out.push({object_id: it.item_id_plain || it.item_id, title: it.title || '',
                  comments});
      }
      return {err: 0, feeds: out};
    }"""

    @staticmethod
    def _match_work(works: list[dict], title: str) -> dict | None:
        """平台标题和本地标题按前缀/包含关系比对（两边描述常有一边被截断）。"""
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
        page.goto(self.COMMENT_PAGE, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        data = page.evaluate(self.FETCH_COMMENTS_JS) or {}
        if data.get("err"):
            return {}, f"评论接口返回错误码 {data['err']}（登录态可能失效）"
        out: dict[str, tuple[dict, list[dict]]] = {}
        for feed in data.get("feeds") or []:
            # 作品标题以接口返回为准：本地数据页抓到的标题经常是播放量数字
            w = self._match_work(works, feed.get("title") or "") or {
                "work_id": "dy" + (feed.get("object_id") or ""),
                "work_title": (feed.get("title") or "")[:80],
                "work_url": "",
            }
            w = dict(w, work_title=(feed.get("title") or w.get("work_title") or "")[:80])
            out[w["work_id"]] = (w, feed.get("comments") or [])
        return out, ""

    # ---------- 一键回复 ----------
    # 回复接口抓包自创作者评论页：POST JSON，字段 comment_Id（大写 I）/
    # item_id（加密 item_id，从 item/list 拿）/text。csrf、msToken 不传也能过。
    REPLY_JS = r"""async t => {
      const g = async u => (await (await fetch(u, {credentials: 'include'})).json());
      const norm = s => (s || '').replace(/\s+/g, '');
      // work_id 可能是「dy+item_id_plain」的兜底形态，先解析出加密 item_id
      const wid = t.work_id || '';
      const key = norm(t.title);
      let it = null;
      // 作品可能比较老，翻几页找（与采集一致的分页逻辑）
      let cursor = 0;
      for (let pg = 0; pg < 3 && !it; pg++) {
        const items = await g('/aweme/v1/creator/item/list/?cursor=' + cursor
                              + '&count=20&aid=2906');
        const list = items.item_info_list || [];
        it = list.find(x => x.item_id === wid)
          || list.find(x => x.item_id_plain && wid.endsWith(x.item_id_plain))
          || list.find(x => {
            const lt = norm(x.title || '');
            return lt && key && (lt.slice(0, 12) === key.slice(0, 12)
              || lt.includes(key.slice(0, 10)) || key.includes(lt.slice(0, 10)));
          });
        if (!items.has_more) break;
        cursor = items.cursor || (cursor + list.length);
      }
      if (!it) return {ok: false, err: '作品匹配失败（可能已删除或标题改动）'};
      const r = await fetch(
        'https://creator.douyin.com/aweme/v1/creator/comment/reply/?aid=2906', {
          method: 'POST', credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({comment_Id: t.comment_id, item_id: it.item_id,
                                text: t.text})});
      const resp = JSON.parse(await r.text());
      return resp && resp.status_code === 0 ? {ok: true}
        : {ok: false, err: (resp && resp.status_msg) || '接口返回错误'};
    }"""

    def reply_comment(self, ctx, target: dict, text: str) -> tuple[bool, str]:
        """把 text 回复到 target（评论）下。返回 (是否成功, 错误信息)。"""
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(self.COMMENT_PAGE, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return False, "打开抖音后台失败（登录态可能失效）"
        page.wait_for_timeout(10000)
        res = page.evaluate(self.REPLY_JS, {
            "work_id": target.get("work_id") or "",
            "title": target.get("work_title") or "",
            "comment_id": target.get("comment_key") or "",
            "text": text,
        }) or {}
        if res.get("ok"):
            return True, ""
        return False, res.get("err") or "回复发送失败"

    # ---------- 私信采集 ----------
    # 抖音 IM 走 protobuf（imapi.douyin.com），解析成本高且脆；改走 DOM：
    # www 主站点「消息」下拉里直接渲染会话列表（名称/时间/最后一条/未读数）。
    MSG_LIST_JS = r"""() => {
      const items = [...document.querySelectorAll(
        '[class*=conversationConversationItemwrapper]')]
        .filter(e => e.offsetHeight > 0);
      return items.slice(0, 20).map(e => {
        const lines = (e.innerText || '').split('\n')
          .map(x => x.trim()).filter(Boolean);
        const unread = lines.length > 3 && /^\d+$/.test(lines[lines.length - 1])
          ? parseInt(lines.pop(), 10) : 0;
        return {
          sender: lines[0] || '',
          time: lines[1] || '',
          content: lines.slice(2).join(' '),
          unread,
        };
      });
    }"""

    def collect_messages(self, ctx) -> tuple[list[dict], str]:
        """读私信会话的最近消息。返回 (标准条目列表, 错误信息)。"""
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded",
                      timeout=90000)
        except Exception:
            return [], "打开抖音主站失败（登录态可能失效）"
        page.wait_for_timeout(9000)
        try:
            page.click("text=消息", timeout=8000)
        except Exception:
            return [], "找不到消息入口（页面改版或未登录）"
        page.wait_for_timeout(8000)
        rows = page.evaluate(self.MSG_LIST_JS) or []
        items: list[dict] = []
        for r in rows:
            sender = (r.get("sender") or "").strip()
            content = (r.get("content") or "").strip()
            if not sender:
                continue
            items.append({
                "sender": sender[:40],
                "content": content[:200],
                # 没有平台消息 ID，用「会话+预览」指纹去重
                "msg_key": f"{sender}:{content[:30]}",
                "created_text": (r.get("time") or "")[:20],
                "unread_count": int(r.get("unread") or 0),
                "is_group": "群" in sender,
            })
        return items, ""
