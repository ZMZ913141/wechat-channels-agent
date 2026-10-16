"""账号管理：新增/扫码登录/登录态实测/切换内核。"""
import html
import os
import subprocess
import sys

from flask import redirect, render_template, request, url_for

from .. import accounts, config, loginprobe, login_state, proclock
from .base import app, BASE_DIR

CREATE_NEW_CONSOLE = 0x00000010 if sys.platform == "win32" else 0


def login_badge_html(aid: str) -> str:
    """账号的登录态徽章：显示**实测登录的视频号昵称** + 可信度标记。

    多账号最隐蔽的事故是两个号登成了同一个视频号，这里直接显示昵称就能看出来。
    昵称是占位符（"微信用户"）时**明确标红警示**，绝不把它当身份显示——
    否则三个号会全显示"微信用户"，等于没有辨识度。
    """
    from .. import loginprobe as _lp

    acc = accounts.get_account(aid)
    if not acc:
        return ""
    d = _lp.display_name(acc)
    esc = html.escape(str(d["primary"]))
    if d["verified"]:
        badge = (
            f'<div class="lrow ok"><span class="lname">{esc}</span>'
            f'<span class="ltag ok" title="已实测确认：这是该档案实际登录的视频号">已确认</span></div>'
        )
    elif d["placeholder"]:
        badge = (
            f'<div class="lrow bad"><span class="lname">{esc}</span>'
            f'<span class="ltag bad" title="昵称显示为平台默认占位，说明该档案未真正登录">'
            f'未真正登录</span></div>'
            f'<div class="muted" style="font-size:10.5px">实测昵称为占位符，请重新扫码</div>'
        )
    elif d["secondary"].startswith("已登录"):
        badge = (
            f'<div class="lrow warn"><span class="lname">{esc}</span>'
            f'<span class="ltag warn">昵称未读到</span></div>'
        )
    elif d["known"]:
        badge = (
            f'<div class="lrow bad"><span class="lname">{esc}</span>'
            f'<span class="ltag bad">未登录</span></div>'
        )
    else:
        badge = '<div class="muted" style="font-size:11px">尚未检测</div>'
    at = html.escape(str(d.get("at") or ""))
    stamp = f'<div class="muted" style="font-size:10px">检测于 {at}</div>' if at else ""
    return badge + stamp


@app.route("/accounts")
def accounts_page():
    cur = accounts.current_account_id()
    msg = ""
    m = request.args.get("msg", "")
    if m == "ok":
        msg = ('<div class="msg ok">账号已添加（浏览器档案目录已创建）。'
               '点「扫码登录」用对应微信号登录后即可使用。</div>')
    elif m == "login":
        msg = ('<div class="msg ok">已打开扫码窗口（新弹出的控制台窗口有操作提示）。'
               '用微信扫描页面二维码并确认，控制台出现「登录成功」即可。'
               '完成后<b>关闭浏览器窗口</b>，登录态就保存好了。</div>')
    elif m.startswith("probe:"):
        tip = m[6:]
        cls = "ok" if tip.startswith("已登录") else "bad"
        msg = f'<div class="msg {cls}">{html.escape(tip)}</div>'
    elif m == "channel":
        msg = ('<div class="msg ok">已更新浏览器内核。下次打开窗口（扫码登录 / 打开发表页 / 发布）'
               '就会用新内核；正在运行的窗口不受影响。</div>')
    elif m.startswith("err:"):
        msg = f'<div class="msg bad">操作失败：{html.escape(m[4:])}</div>'
    return render_template(
        "accounts.html",
        rows=accounts.load_accounts(),
        cur=cur,
        msg=msg,
        channels=accounts.CHANNEL_LABELS,
        title="账号管理",
    )


@app.route("/accounts/new", methods=["POST"])
def accounts_new():
    try:
        aid = (request.form.get("id") or "").strip()
        name = (request.form.get("name") or "").strip()
        if not aid:
            raise ValueError("账号 ID 不能为空")
        if accounts.get_account(aid):
            raise ValueError(f"账号 ID「{aid}」已存在")
        acc = accounts.add_account({
            "id": aid,
            "name": name or aid,
            "profile_dir": request.form.get("profile_dir", "").strip(),
            "channel": request.form.get("channel", "").strip().lower(),
            "enabled": request.form.get("enabled", "true").lower() == "true",
        })
        try:
            accounts.profile_path(acc)   # 确保目录存在
        except Exception:
            pass
        return redirect(url_for("accounts_page", msg="ok"))
    except ValueError as e:
        return redirect(url_for("accounts_page", msg="err:" + str(e)))


@app.route("/accounts/<aid>/login")
def accounts_login(aid):
    if not accounts.get_account(aid):
        return redirect(url_for("accounts_page"))
    # 扫码登录需要独占浏览器档案：打暂停标记，正在进行的发布会主动避开
    proclock.pause(aid, reason="扫码登录", owner="manual")
    # open_publish.py 通过环境变量 ACCOUNT_ID 接收账号
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
           "ACCOUNT_ID": aid}
    subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "open_publish.py"), "--login"],
        cwd=BASE_DIR, env=env, creationflags=CREATE_NEW_CONSOLE,
    )
    return redirect(url_for("accounts_page", msg="login"))


@app.route("/accounts/<aid>/loginlog")
def accounts_loginlog(aid):
    """查看该账号最近一次扫码/打开的日志。

    为什么要这个页面：面板用 CREATE_NEW_CONSOLE 拉起登录脚本，脚本一旦
    出错那个窗口会一闪而过。日志落盘后用户能自己看到原因，不用猜。
    """
    path = os.path.join(config.STATE_DIR, f"login_{aid}.log")
    if not os.path.exists(path):
        return render_template(
            "loginlog.html", aid=aid, name=(accounts.get_account(aid) or {}).get("name", aid),
            content="（还没有日志。点「扫码登录」后这里会出现记录。）", title="登录日志")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception as e:
        lines = [f"读取失败：{e}"]
    return render_template(
        "loginlog.html",
        aid=aid,
        name=(accounts.get_account(aid) or {}).get("name", aid),
        content="\n".join(lines[-200:]) or "（日志为空）",
        title="登录日志",
    )


@app.route("/accounts/<aid>/probe")
def accounts_probe(aid):
    """无头实测登录态与实际视频号昵称。"""
    acc = accounts.get_account(aid)
    if not acc:
        return redirect(url_for("accounts_page"))
    try:
        res = loginprobe.probe_account(acc)
    except Exception as e:
        return redirect(url_for("accounts_page", msg="err:" + str(e)[:60]))
    if res.get("logged_in"):
        tip = "已登录：" + (res.get("nickname") or "（读不到昵称）")
    else:
        tip = "未登录（" + (res.get("detail") or "未知原因") + "），请扫码"
    return redirect(url_for("accounts_page", msg="probe:" + tip))


@app.route("/accounts/<aid>/resume")
def accounts_resume(aid):
    """手动解除「正在操作中」的暂停标记。"""
    proclock.resume(aid)
    return redirect(url_for("accounts_page"))


@app.route("/accounts/<aid>/channel", methods=["POST"])
def accounts_set_channel(aid):
    acc = accounts.get_account(aid)
    if acc:
        accounts.update_account(aid, {"channel": request.form.get("channel", "").strip().lower()})
    return redirect(url_for("accounts_page", msg="channel"))


@app.route("/accounts/<aid>/set_current")
def accounts_set_current(aid):
    if accounts.get_account(aid):
        accounts.set_current_account(aid)
    return redirect(url_for("index"))


@app.route("/accounts/<aid>/delete")
def accounts_delete(aid):
    accounts.delete_account(aid)
    return redirect(url_for("accounts_page"))


@app.route("/open_publish")
def open_publish():
    """打开某账号的视频号发表页（登录态独占，不会和发布任务打架）。"""
    aid = request.args.get("account") or accounts.current_account_id()
    if not accounts.get_account(aid):
        return redirect(url_for("index"))
    proclock.pause(aid, reason="手动打开发表页", owner="manual")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
           "ACCOUNT_ID": aid}
    subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "open_publish.py")],
        cwd=BASE_DIR, env=env, creationflags=CREATE_NEW_CONSOLE,
    )
    return redirect(url_for("index"))