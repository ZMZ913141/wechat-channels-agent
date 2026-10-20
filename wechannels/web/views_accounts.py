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
        msg = ('<div class="msg ok">已打开扫码窗口。'
               '如果<b>没有看到浏览器窗口</b>，请到任务栏找一下 Edge / Chrome 图标'
               '（它可能被这个控制台窗口挡住了）。'
               '用微信扫描页面二维码并确认，控制台出现「登录成功」即可；'
               '完成后<b>关闭浏览器窗口</b>，登录态就保存好了。</div>')
    elif m == "busy":
        msg = ('<div class="msg warn">这个账号的浏览器档案正被占用，'
               '所以没有重复打开窗口——<b>一个账号同时只能开一个浏览器窗口</b>。<br>'
               '· 如果上次打开的窗口还在（可能被挡在后面）：直接切到那个窗口扫码即可；<br>'
               '· 如果那个窗口已经关了：等几秒再点一次「扫码登录」；<br>'
               '· 如果该账号正在发布：等发布结束后再登录。</div>')
    elif m == "lastacct":
        msg = ('<div class="msg bad">不能删除最后一个账号——'
               '删掉后面板就没有可用的视频号了。'
               '如果只是想换一个，请先「新增」另一个账号再删这个。</div>')
    elif m.startswith("del:"):
        nm = m.split(":", 1)[1]
        msg = (f'<div class="msg ok">已删除账号「{html.escape(nm)}」。'
               '浏览器档案目录已保留；若以后重建同名账号，'
               '原来的登录态可能还能用。</div>')
    elif m == "nodup":
        msg = '<div class="msg bad">该账号不存在，可能已被删除。</div>'
    elif m.startswith("probe:"):
        tip = m[6:]
        cls = "ok" if tip.startswith("已登录") else "bad"
        msg = f'<div class="msg {cls}">{html.escape(tip)}</div>'
    elif m == "channel":
        msg = ('<div class="msg ok">已更新浏览器内核。下次打开窗口（扫码登录 / 打开发表页 / 发布）'
               '就会用新内核；正在运行的窗口不受影响。</div>')
    elif m == "channelcleared":
        msg = ('<div class="msg ok">已清空该账号的旧浏览器档案并切换内核。'
               '旧档案已备份到 <code>state/profile_backups/</code>；'
               '因为换了浏览器内核，<b>需要重新扫码登录</b>一次。</div>')
    elif m.startswith("needclear:"):
        msg = ('<div class="msg bad">⚠️ 该账号已经登录过，直接换浏览器内核会打不开它的旧档案'
               '（格式不兼容，表现为 exitCode=21）。<br>'
               '请点该行的<b>「切换内核」</b>并按提示确认——会自动备份并清空旧档案，'
               '然后<b>重新扫码登录</b>。<br>'
               '<span class="muted">（若只是想换个浏览器又不介意重登，也可以直接点「清空档案」。）</span></div>')
    elif m.startswith("cleared:"):
        msg = (f'<div class="msg ok">已清空账号「{html.escape(m[8:])}」的浏览器档案'
               '（旧档案已备份到 <code>state/profile_backups/</code>），'
               '请重新<b>扫码登录</b>。</div>')
    elif m.startswith("err:"):
        msg = f'<div class="msg bad">操作失败：{html.escape(m[4:])}</div>'
    return render_template(
        "accounts.html",
        rows=accounts.load_accounts(),
        cur=cur,
        msg=msg,
        channels=accounts.CHANNEL_LABELS,
        distinct=accounts.kernel_distinct_status(),
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
    acc = accounts.get_account(aid)
    if not acc:
        return redirect(url_for("accounts_page"))
    # 一个浏览器档案同时只能被一个进程打开。若已有窗口占着，再点「扫码登录」
    # 只会让新进程静默等锁（最多 45 秒）然后报错——用户看到的就是「卡住了」。
    # 所以这里先看一眼占用者，有就直说，不白开一个控制台窗口。
    try:
        holder = proclock.read_holder(accounts.profile_path(acc))
    except Exception:
        holder = None
    if holder:
        return redirect(url_for("accounts_page", msg="busy"))
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
    """切换浏览器内核。**已登录过的账号必须先清空旧档案**才能切，否则打不开。"""
    acc = accounts.get_account(aid)
    if not acc:
        return redirect(url_for("accounts_page", msg="nodup"))
    new_ch = (request.form.get("channel") or "").strip().lower()
    clear = (request.form.get("clear") or "0").strip().lower() in ("1", "true", "yes", "on")
    if accounts.channel_switch_needs_clear(acc, new_ch):
        if not clear:
            # 没确认清空 → 拒绝，回面板给出解释（绝不让用户「以为切了其实没切」）
            return redirect(url_for("accounts_page", msg="needclear:" + aid))
        r = accounts.clear_profile(acc)
        if not r.get("ok"):
            return redirect(url_for("accounts_page", msg="err:清空旧档案失败：" + (r.get("error") or "")))
    accounts.update_account(aid, {"channel": new_ch})
    return redirect(url_for("accounts_page", msg=("channelcleared" if clear else "channel")))


@app.route("/accounts/<aid>/clearprofile", methods=["POST"])
def accounts_clear_profile(aid):
    """清空该账号浏览器档案（登录态/缓存），强制重新扫码。

    这是「不同账号/不同浏览器之间不串数据」的强制落地：想换内核、怀疑缓存被
    搞乱、或要把账号迁到另一个浏览器时，都先清空，避免旧 Cookie/指纹残留。
    """
    acc = accounts.get_account(aid)
    if not acc:
        return redirect(url_for("accounts_page", msg="nodup"))
    r = accounts.clear_profile(acc)
    if not r.get("ok"):
        return redirect(url_for("accounts_page", msg="err:清空档案失败：" + (r.get("error") or "")))
    return redirect(url_for("accounts_page", msg="cleared:" + (acc.get("name") or aid)))


@app.route("/accounts/<aid>/set_current")
def accounts_set_current(aid):
    if accounts.get_account(aid):
        accounts.set_current_account(aid)
    return redirect(url_for("index"))


@app.route("/accounts/<aid>/delete", methods=["POST"])
def accounts_delete(aid):
    """删除账号。

    ⚠️ 必须 POST：删账号的代价最高——浏览器档案里的登录态一起没了，
    要重新扫码。用 GET 的话链接预览/预取就可能把账号删了。
    另外：最后一个账号不允许删，否则面板进入无账号的死状态。
    """
    acc = accounts.get_account(aid)
    if not acc:
        return redirect(url_for("accounts_page", msg="nodup"))
    if len(accounts.load_accounts()) <= 1:
        return redirect(url_for(
            "accounts_page",
            msg="lastacct"))   # 模板会渲染成醒目警告
    accounts.delete_account(aid)
    return redirect(url_for("accounts_page", msg="del:" + aid))


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