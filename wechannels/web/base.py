"""Flask 应用实例与扩展。

重构说明：
此前 app.py 是 2000+ 行单文件，把路由、CSS、JS、HTML 字符串全塞在一起，
页面靠 `html.replace("{placeholder}", ...)` 拼接 —— 改版时非常脆弱，
IDE 也无法给 HTML/JS 做语法高亮。

现在拆成 web 包：
    web/base.py            应用实例、扩展初始化、共用上下文
    web/views_*.py         按业务分模块的路由
    web/templates/*.html   真正的 Jinja2 模板
    web/static/*           CSS / JS 独立文件（可被浏览器缓存）
"""
import os

from flask import Flask

from .. import config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(WEB_DIR, "templates"),
    static_folder=os.path.join(WEB_DIR, "static"),
    static_url_path="/static",
)
app.config["JSON_AS_ASCII"] = False

# 本工具绑在 127.0.0.1 上、只在本机使用，没有多用户，因此不引入登录态与 CSRF。
# ⚠️ 若将来要改host="0.0.0.0" 让局域网访问，**必须先加鉴权**——
# /media 与 /serve 会直接读本地文件，暴露出去等于任意文件读取。
app.config["MAX_CONTENT_LENGTH"] = 2048 * 1024 * 1024  # 单次上传上限 2GB


@app.context_processor
def inject_globals():
    """所有模板都能直接用的变量。"""
    return {
        "APP_NAME": "视频号助手",
        "now_year": __import__("datetime").datetime.now().year,
    }