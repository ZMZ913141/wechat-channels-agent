"""视频号发表页所有选择器集中在此。

微信改版时只改这个文件。
基于 2026-10 实测：表单在 wujie 微前端 iframe 里，上传控件在 page.frames 中。
"""

# 发表页
PUBLISH_URL = "https://channels.weixin.qq.com/platform/post/create"

# 内容管理页（成功判定：URL 跳到这里）
POST_LIST_URL = "https://channels.weixin.qq.com/platform/post/list"

# 上传：input[type=file] 可能在主页面或任意 iframe 中
FILE_INPUT = 'input[type="file"]'

# 表单 iframe（微前端）
FORM_IFRAME_URL_CONTAINS = "micro/content/post/create"

# 表单字段（在 iframe 内）
SHORT_TITLE_INPUT = 'input[placeholder*="短标题"], input[class*="title"]'
DESCRIPTION_EDITABLE = 'div.input-editor, [contenteditable]:not([contenteditable="false"])'
TOPIC_INPUT = 'input[placeholder*="话题"], input[class*="topic"]'

# 复选框/按钮
ORIGINAL_CHECKBOX = 'div[class*="original"] input[type="checkbox"], label:has-text("声明原创")'
AI_DISCLOSURE_SELECT = 'div[class*="disclosure"], div[class*="label"]:has-text("视频标注")'
PUBLISH_BUTTON = 'button:has-text("发表"), button:has-text("发布")'

# 定时发表
SCHEDULE_TOGGLE = 'div:has-text("定时发表"), label:has-text("定时")'

# 错误提示（用于判定失败）
ERROR_TOAST = 'div[class*="error"], div[class*="toast"][class*="error"]'
