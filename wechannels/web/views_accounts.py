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
    """账号的登录态徽章：cookie 启发式 + 实测身份。

    实测身份是关键——多账号最隐蔽的事故是两个号登成了同一个视频号，
    这里直接显示昵称，一眼就能看出有没有登错。
    """
    acc = accounts.get_account(aid)
    if not acc:
        return ""
    try:
        st = login_state.status(accounts.profile_path(acc))
    except Exception:
        st = {"logged_in": False, "fresh": False, "detail": ""}
    cls = "ok" if (st["logged_in"] and st["fresh"]) else ("warn" if st["logged_in"] else "bad")
    txt = "已登录" if (st["logged_in"] and st["fresh"]) else ("可能失效" if st["logged_in"] else "未登录")

    ident = loginprobe.identity_summary(aid)
    nick = ""
    if ident.get("known"):
        stale = "（较旧）" if ident.get("stale") else ""
        if ident.get("logged_in"):
            nick = (f'<div class="muted" style="font-size:11px">实测视频号：'
                    f'<b>{html.escape(str(ident.get("nickname") or "（读不到昵称）"))}</b>'
                    f'{stale}</div>')
        else:
            nick = f'<div class="bad" style="font-size:11px">实测未登录：{html.escape(str(ident.get("detail", "")))}</div>'
    return (
        f'<span class="dot {cls}" title="{html.escape(str(st.get("detail", "")))}"></span>'
        f'<span class="muted" style="font-size:11px">{txt}</span>{nick}'
    )


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
               '用微信扫描页面二维码并确认，控制台出现「登录成功」即可。</div>')
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
    proclock.pause(aid, reason="扫码登录")
    # open_publish.py 通过环境变量 ACCOUNT_ID 接收账号
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
           "ACCOUNT_ID": aid}
    subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "open_publish.py"), "--login"],
        cwd=BASE_DIR, env=env, creationflags=CREATE_NEW_CONSOLE,
    )
    return redirect(url_for("accounts_page", msg="login"))


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
    proclock.pause(aid, reason="手动打开发表页")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
           "ACCOUNT_ID": aid}
    subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "open_publish.py")],
        cwd=BASE_DIR, env=env, creationflags=CREATE_NEW_CONSOLE,
    )
    return redirect(url_for("index"))