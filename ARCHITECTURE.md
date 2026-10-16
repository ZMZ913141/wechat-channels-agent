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

## 三、私信/评论监控与通知：已移除（决策记录）

本项目早期版本包含私信与评论监控 + 四渠道推送 + 固定规则自动回复，现已**整块移除**，
聚焦「多账号管理 + 发布 + 素材库」。移除原因：

1. **合规与风控风险最高**。自动回复属「技术手段模拟真人互动」，
   2026-09-02 网信办通报案例 6 明确处置了「AI 托管软件模拟真人运营、评论互动、生成同质文案」的账号。
2. **价值密度低**。抓到的内容最终仍要人看，而面板里直接看更高效，无需中转推送。
3. **与发布争抢资源**。监控和发布抢同一个浏览器档案，是此前「明明登录过却显示未登录」
   以及「连续发布必然失败」等问题的根源之一。移除后多账号发布干净了。

### 如果将来要恢复，值得知道的业界最佳实践

没有官方接收 API，也没有 SSE/WebSocket 推送，所以**长轮询风险高于低频短请求**。
`channels-watch`（只读私信监控）的做法值得逐字抄：

- **只读列表快照做 diff，不点开会话** —— 行为特征最接近"人扫一眼后台"
- 识别两类变化：新会话 + **已读→未读回退**（能抓到"对方又发了但你没读"）
- 比较前**剥掉"刚刚/5分钟前"这类相对时间**，否则每次重渲染都误报
- 用内容指纹（`state/seen_<id>.json`）做增量，**不用游标**（没有游标可用）
- 低频：默认 5 分钟一次

推送通道若要恢复，首选**企业微信自建应用消息**（免费、额度宽、按 `touser` 精确投）；
**别用 Server 酱做告警主通道**——免费版仅 5 条/天，多号轮询一圈就打满。

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

## 五、多账号为什么不会互相冲突

这是本项目的核心设计，值得单独说明。**两层锁**，缺一不可：

| 锁 | 文件 | 保护什么 | 不加会怎样 |
|---|---|---|---|
| **账号锁** | `state/task_<id>.lock` | 同一账号不被两个发布任务同时提交 | 同一个视频号被重复发稿 |
| **档案锁** | `state/profile_<hash>.lock` | 一个 Chromium 档案不被两个进程同时打开 | 后开的**拿不到登录态**，只剩二维码，表现为「明明刚登录过却显示未登录」 |

**不同账号之间天然不冲突**：每个账号一个独立 `user_data_dir`，
档案锁按路径哈希计算，所以账号 A 的锁不会挡住账号 B。实测已验证三条：

1. 3 个账号档案路径互不相同 → 串行发布时每个都能拿到锁 ✓
2. 同一账号第二个任务被拒，且能看到占用者是谁（进程+起始时间）✓
3. 进程被强杀后的残留锁能自动识别并清理，不会永久卡死 ✓

还有一层**手动操作暂停窗口**：面板「扫码登录 / 打开发表页」会给该账号打一个
暂停标记，发布侧检测到就提前拒绝并提示「请约 N 分钟后再发布」——
而不是让你等一个晦涩的「档案被占用」错误。

### 怎么确认两个号没登成同一个

多账号的头号事故是**两个档案登成了同一个视频号**（矩阵直接失效却毫无察觉）。
面板「账号管理 → 检测登录」会无头实测并显示**实际登录的视频号昵称**，
一眼就能看出有没有登错。原理见 `wechannels/loginprobe.py`。

> 注意：为什么必须用「发表页」而不是 `/platform` 判断登录态——
> 实测**登录成功后**直接开 `/platform` 也会被跳到 `login.html`，会把已登录误判成未登录。

---

## 六、项目结构与已知短板

### 代码组织

```
app.py                       入口：装配 + 启动（~60 行，无业务逻辑）
open_publish.py              CLI：打开发表页 / 扫码登录
wechannels/
  config.py                  配置（全部来自 .env，带默认值）
  accounts.py                多账号管理
  proclock.py                两层锁：账号锁 + 档案锁
  materials.py               素材库数据层（含标签/去重/孤儿检测）
  ledger.py                  发布记录（batch/target 三态模型，看板数据源）
  stats.py                   统计聚合（口径集中在此，便于校验）
  migrate.py                 旧 publish_history → ledger 的一次性迁移（幂等）
  publish.py                 发布编排（多账号串行 + 任务状态）
  publish_pw.py              确定性发布（Playwright 直填表单）
  launcher.py                有头浏览器打开 / 扫码登录
  loginprobe.py              登录态探针（显示实际登录的视频号昵称）
  login_state.py             cookie 启发式登录态判断
  media_probe.py             视频合规预检（ffprobe）
  autofill.py                AI 智能填写文案
  browser.py                 browser-use 工厂（回退方案用）
  web/
    base.py                  Flask 实例
    templating.py            Jinja2 全局函数
    views_core.py            首页 / 素材 CRUD / 媒体服务
    views_library.py         维护页 / 目录导入
    views_accounts.py        账号管理
    views_publish.py         发布中心
    views_dashboard.py       数据看板 + CSV 导出
    templates/               base + 7 页面 + partials + macros
    static/                  app.css + JS；vendor/echarts.min.js（本地化，Apache-2.0）
```

**为什么是 Jinja2 而不是字符串拼接**：早期 `app.py` 把路由、CSS、JS、HTML 混在
一个文件里，用 `html.replace('{placeholder}', ...)` 拼页面——改版时极脆弱，
IDE 也无法为 HTML/JS 做语法高亮。现在模板与静态资源分离，CSS/JS 可被浏览器缓存。

### 已知短板

| 短板 | 影响 | 状态 |
|---|---|---|
| 定时发表/封面依赖 DOM 文本匹配 | 视频号改版可能失效 | 已做「未生效如实回报」+ 失败截图兜底 |
| 发布为**串行**，多账号需排队 | 号多时耗时长 | 刻意不并行：并行会放大风控与带宽风险 |
| 无播放量/点赞等数据 | 无法做内容效果分析 | **平台无接口**，只能看发布侧数据 |
| 无 `secret_key`/CSRF | 若改 `host=0.0.0.0` 即任意文件读取 | 已加启动警告；保持绑 127.0.0.1 |
| 状态持久化用 JSON 文件 | 并发写有理论风险 | ledger 已用「临时文件 + os.replace」原子写；单进程使用 |
| 素材库无全文检索/缩略图缓存 | 素材上千后筛选变慢 | 现有标签+状态+关键词已够用 |

---

## 七、来源清单

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