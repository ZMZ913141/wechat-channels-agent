# 架构决策：视频号自动化方案调研与选型

> 这份文档回答三个问题：**有没有官方 API？业界怎么做？我们为什么这么选？**
> 调研时间：2026-10-16。结论分「官方文档确认」与「社区经验」两类，已分别标注。

---

## 一、先说结论：没有官方发布 API

微信开放平台视频号文档（https://developers.weixin.qq.com/doc/channels/）的能力目录里，
只有**小店商品、罗盘数据、优选联盟、会员、微信客服**——**没有**普通视频的
草稿/发布/评论/私信接口。

需要特别澄清两个容易踩的坑：

| 容易误以为存在的接口 | 实际情况 |
|---|---|
| `cgi-bin/channels/ec/video/upload`（网上流传的"视频号上传 API"） | 官方文档无此接口，部分第三方站点内容不可靠 |
| 公众号 `draft/add` 草稿箱能写视频号 | 不能。视频号与公众号是不同产品、不同上传协议、不同审核链路 |
| 小店商品视频上传接口 | 服务于商品/售后资源，不等于普通视频动态 |
| 视频号评论/私信读取 API | **不存在**。评论管理、私信都只能靠网页读取 |

**唯一的官方私信通道是「微信客服」**，但有硬门槛：需企业认证 + 绑定企业微信 + 开通小店，
且用户只能从客服入口发消息（**拿不到"创作者收到的全部私信"**），下发还限制 5 条/48 小时。

> **所以：浏览器自动化不是"过渡方案"，而是当前业界唯一可选项。**
> 这个结论很重要——它决定了这个项目的技术路线，也决定了下面的风险讨论。

---

## 二、业界怎么做：四条路线对比

| | A. 纯 Playwright 选择器 | B. Playwright + CDP | C. 私有接口重放 | D. 第三方 SaaS |
|---|---|---|---|---|
| 可靠性 | 中，改版即挂 | 中高，上传不受 shadow 影响 | 高（登录态有效期内） | 高 |
| 可维护性 | 差 | 中 | **差**（逆向成本最高） | 好但不可定制 |
| 封号风险 | 高 | 高 | 最高（无 Referer 行为链） | 低（授权接入） |
| 成本 | 零 token | 零 token | 逆向人力 | 订阅费 |

**我们选 A 为主 + B 仅作上传兜底**，理由：

1. **A/B 的可靠性差距远小于维护成本差距**。发布表单 90%（短标题、描述、话题、定时、
   合集、可见范围）都要 DOM 操作，跑不掉 A 的全部维护成本。B 只在"上传文件"这一个点有增量价值。
2. **B 会绑死 Chromium 并撞上 Chrome 136+ 的远程调试限制**，且 `connectOverCDP` 保真度低于原生协议。
3. **C 的性价比不成立**：读侧（评论/私信列表）确实比 DOM 解析稳，但要维护两套栈；
   发布侧至今**没有公开实现**（签名 + 分片上传 + 转码轮询全是私有协议）。3–10 个号的规模撑不起逆向成本。
4. **D 不划算**：单视频号场景下订阅费 + 数据出境 + 定制受限；只在「发布失败率已不可接受」
   或「要扩到多平台（抖音/小红书/B站）」时才值得考虑。

### 值得抄的业界经验

- **`dreamsim/social-auto-upload`**（15.5k★，视频号模块 1244 行）的事实教训：
  - 上传控件要**遍历 `page.frames`** —— 视频号是无界(wujie)微前端，表单和上传框都在 iframe 里。
  - 大文件上传实测教训：172MB / 上行 0.5Mbps 时传到一半失败。**必须硬超时 + 重试上限 + 失败截图**。
  - 短标题有 **7-15 字硬校验**，不满足会被直接拦下提交。
  - headless 下拉项在滚动容器里点不中 → `scroll_into_view` → `click(force=True)` → `dispatch_event` 三级兜底。
- **`channels-watch`** 的读侧最优实践：**只读列表快照做 diff，不点开会话**。
  识别两类变化——新会话 + 已读→未读回退；比较前**剥掉"刚刚/5分钟前"这类相对时间**，
  否则每次重渲染都误报。README 自述"会话可能被服务端撤销，快照法能减少触发风控的交互面"。
- **绝不要开 stealth/指纹伪装**。运营规范 4.7「恶意对抗」是**独立违规项**，比 4.3 更重。
  用真实 Chrome + 本地档案反而更安全——这也正是主流项目的做法。

---

## 三、私信与评论：为什么只能轮询

没有官方接收 API，也没有 SSE/WebSocket 推送通道，所以**长轮询风险高于低频短请求**。

我们的做法（`monitor.py` + `monitor_pw.py`）：

- 低频轮询（默认 300s），**只读列表不点开会话** —— 行为特征最接近"人扫一眼后台"
- 单账号串行，档案独占锁防打架
- 用内容指纹（`state/seen_<id>.json`）做增量，**不用游标**（没有游标可用）
- 评论判定**基于 DOM 结构**（评论类名 / 「N条评论」计数），**绝不写死业务关键词**
  —— 这是本项目修掉的一个真实隐患：老代码里硬编码了「新中式|雅太社|供货|女装」，
  那是当时账号的类目，换任何一个其它垂直的号评论抓取就直接失效。

### 通知渠道怎么选（多账号 / 中文 / 免费 / 免公网 IP）

| 渠道 | 免费额度 | 结论 |
|---|---|---|
| **企业微信自建应用消息** | 免费，额度宽，按 `touser` 精确投 | **最优**（已实现），支持每账号分流 |
| 企业微信「消息推送」群机器人 | 免费，20 条/分钟 | 次优，适合团队共享 |
| PushPlus | 200 条/天 | 够用，兜底 |
| Server酱 | **仅 5 条/天** | ❌ 多号轮询一圈就爆，别做告警主通道 |

> **注意**：本项目所有推送已加**指数退避重试 3 次**（含企微 token 过期自动清缓存重取）。
> 通知是这个项目的核心产出，单次发送失败等于丢消息。

---

## 四、风控与合规（请务必读完）

**红线条款**（https://weixin.qq.com/cgi-bin/readtemplate?t=weixin_agreement&s=video）：
- **4.3 使用外挂**：未经书面许可使用插件/第三方工具连接微信系统 —— 这正是浏览器自动化的定义
- **4.7 恶意对抗**：技术手法规避或对抗平台检测 —— stealth/指纹伪装的直接落点，处罚更重
- **6.1 处置**可延伸到"与该主体相关的账号"（连坐）

**真实处罚案例**：2026-09-02 网信办通报案例 6，微信账号因"AI 托管软件模拟真人运营、
评论互动、生成同质文案"被处置。**AI 全托管 + 自动评论回复已被监管点名**。

**降低风险的具体做法**（有依据，非玄学）：
- 请求间隔 0.5–1s，单号串行，一条号同一时刻只开一个 tab
- 低频轮询（5 分钟），只读列表不点开会话
- 固定时段发布，避免多号同分钟齐发
- 登录态本地持久化，减少扫码频次（扫码本身是高风险交互）
- 自动回复保守：同一人只回一次（`AUTO_REPLY_ONCE_PER_USER=true`）+ 避免同质文案

**诚实结论：这些只能降低概率，不能消除风险。** 没有开源项目能声称零风险。

---

## 五、这套架构还剩什么已知短板

诚实列出来，方便你判断是否值得继续投入：

| 短板 | 影响 | 建议 |
|---|---|---|
| `app.py` 2509 行，路由与视图混排 | 难维护 | 拆 Jinja 模板 + 静态资源 |
| 素材管理**无标签/去重/定时清理** | 素材多了难维护 | 优先加标签检索 |
| 每账号通知接收人**后端已支持但面板无 UI** | 配置要改文件 | 补账号页表单 |
| 无 `secret_key`/CSRF | 若改 `host=0.0.0.0` 即任意文件读取 | 保持绑 127.0.0.1，或加鉴权 |
| 读侧仍靠 DOM 解析 | 评论页改版需适配 | 结构化选择器 + 失败截图已就位 |
| 确定性通道的定时/封面依赖 DOM 文本匹配 | 改版可能失效 | 已做"未生效如实回报"兜底 |

---

## 六、来源清单

**官方文档（权威）**
- 视频号开放文档 https://developers.weixin.qq.com/doc/channels/
- 微信客服概述 https://kf.weixin.qq.com/api/doc/path/93304 ｜ 发送消息 https://kf.weixin.qq.com/api/doc/path/94744
- 小店商家客服 https://developers.weixin.qq.com/doc/store/shop/guide/connect/kf.html
- 视频号运营规范（4.2/4.3/4.5/4.7）https://weixin.qq.com/cgi-bin/readtemplate?t=weixin_agreement&s=video

**源码/仓库（一手，可点开读）**
- dreammis/social-auto-upload（15.5k★，多平台自动上传，视频号模块参考实现）
  https://github.com/dreammis/social-auto-upload ｜ 文档 https://sap-doc.nasdaddy.com/docs/tutorial-basics/platform-channels
- nobiyou/wx_channel（2.7k★，客户端代理拦截，只读+下载，**不能发布**）https://github.com/nobiyou/wx_channel
- NanmiCoder/MediaCrawler（67k★，多平台采集，**不含视频号**）https://github.com/NanmiCoder/MediaCrawler
- xhqing/channels-watch（只读私信监控，快照 diff 实践）https://github.com/xhqing/channels-watch
- jefftko/PostFlow（social-auto-upload fork，封面 + 上传域名兜底）https://github.com/jefftko/PostFlow
- dsxksss/wx_video_api（私有 HTTP API 做评论/私信，**不做发布**）https://github.com/dsxksss/wx_video_api

**社区经验（需打折看待）**
- 视频号评论自动回复三条路线 + 网信办案例 https://noobclaw.com/cn/blog/shipinhao-comment-auto-reply-software-2026
- wujie shadow DOM pitfalls 清单 https://skillsmp.com/zh/creators/teamwiseflow/xiaobei/crews-main-skills-expert-wx-channel-tools-wx-channel-publish
- CDP vs Playwright 保真度、Chrome 136 限制 https://lovstudio.ai/blog/wechat-channels-upload-automation
- 通知通道额度对比 https://sct.ftqq.com/message-push