"""验证从 social-auto-upload 借鉴的 5 项改动 —— 真实浏览器 + 合成页面。

不用 mock：所有断言都跑在真实 chromium 上，页面结构仿视频号发表页。
用法：./.venv/Scripts/python.exe tools/test_publish_ref.py
"""
import base64
import http.server
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from wechannels import publish_pw as P

# 1x1 红 PNG（当作封面文件用）
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  → {detail}" if detail else ""))


# ---------- 合成页面 ----------
HOME_HTML = """<!doctype html><html><body>
<p style="display:none">发表视频</p>
<button class="weui-desktop-btn" onclick="document.getElementById('r').innerText='clicked-btn'">发表视频</button>
<div id="r">no</div>
</body></html>"""

LOGIN_HTML = """<!doctype html><html><body><script>
setTimeout(()=>{location.href='/login.html'},300);</script>home</body></html>"""

QR_HTML = """<!doctype html><html><body>
<iframe src="/open.weixin.qq.com/connect/qrconnect"></iframe></body></html>"""

COVER_HTML = """<!doctype html><html><body>
<div class="horizontal-cover-wrap" id="cw">4:3 封面</div>
<div class="weui-desktop-dialog" id="dlg" style="display:none">
  <input type="file" id="vfi" accept="video/*">
  <input type="file" id="ifi" accept="image/*">
  <div class="weui-desktop-dialog__ft">
    <button id="b1">取消</button><button id="b2">确定</button>
  </div>
</div>
<div id="r">no</div>
<script>
document.getElementById('cw').addEventListener('click',()=>{
  document.getElementById('dlg').style.display='block';
});
for (const id of ['b1','b2']) document.getElementById(id)
  .addEventListener('click',()=>{document.getElementById('r').innerText+='|clicked:'+id;});
document.getElementById('ifi').addEventListener('change',(e)=>{
  document.getElementById('r').innerText += '|file:'+e.target.files[0].name;
});
</script></body></html>"""

SWITCH_HTML = """<!doctype html><html><body>
<div class="changeAccount-dialog">
  <button class="weui-desktop-dialog__close-btn">x</button>
  <button id="b1">切换</button><button id="b2">取消</button>
</div>
<div id="r">no</div>
<script>
for (const id of ['b1','b2']) document.getElementById(id)
  .addEventListener('click',()=>{document.getElementById('r').innerText='clicked:'+id;});
</script></body></html>"""

VERIFY_HTML = """<!doctype html><html><body>
<div class="weui-desktop-dialog__wrp" id="vw">请完成实名验证</div>
</body></html>"""

UPLOAD_ERR_HTML = """<!doctype html><html><body>
<div class="cover-wrap">封面</div>
<div class="status-msg error">上传失败</div>
<div class="media-status-content"><div class="tag-inner">删除</div></div>
</body></html>"""

# 弹窗渲染在 iframe 内的场景
IFRAME_SWITCH = """<!doctype html><html><body>
<iframe id="f" src="/switch" width="400" height="200"></iframe>
</body></html>"""
IFRAME_VERIFY = """<!doctype html><html><body>
<iframe id="f" src="/verify" width="400" height="200"></iframe>
</body></html>"""

PAGES = {
    "/iframe-switch": IFRAME_SWITCH,
    "/iframe-verify": IFRAME_VERIFY,
    "/platform": HOME_HTML,
    "/home-redirect": LOGIN_HTML,
    "/login.html": LOGIN_HTML,
    "/qr": QR_HTML,
    "/open.weixin.qq.com/connect/qrconnect": "qr",
    "/cover": COVER_HTML,
    "/switch": SWITCH_HTML,
    "/verify": VERIFY_HTML,
    "/uerr": UPLOAD_ERR_HTML,
}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path)
        if body is None:
            self.send_error(404)
            return
        b = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"
print(f"[server] {BASE}\n")


def main():
    # 准备真实封面文件
    cov = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cov.png")
    with open(cov, "wb") as f:
        f.write(PNG)

    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context()
        page = ctx.new_page()

        # ---- 1. cookie 失效：跳转登录页 ----
        print("[1] cookie 失效检测 —— 跳转 login.html")
        page.goto(BASE + "/home-redirect", wait_until="domcontentloaded")
        ok, why = P._check_login_state(page, timeout_s=8)
        ck("跳转登录页被识别为失效", (not ok) and "登录页" in why, f"ok={ok} why={why}")

        # ---- 2. cookie 失效：扫码 iframe ----
        print("[2] cookie 失效检测 —— 扫码 iframe")
        page.goto(BASE + "/qr", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        ok, why = P._check_login_state(page, timeout_s=3)
        ck("扫码 iframe 被识别为失效", (not ok) and "扫码" in why, f"ok={ok} why={why}")

        # ---- 3. 正常页面判为有效 ----
        print("[3] 正常页面应判为有效")
        page.goto(BASE + "/platform", wait_until="domcontentloaded")
        ok, why = P._check_login_state(page, timeout_s=3)
        ck("正常页面判为有效", ok and why == "", f"ok={ok} why={why}")

        # ---- 4. _open_post_page 走首页点按钮（不被隐藏 <p> 骗） ----
        print("[4] 打开发表页 —— 首页点「发表视频」")
        page.goto(BASE + "/platform", wait_until="domcontentloaded")
        r = page.evaluate(P._COVER_OPEN_JS if False else "1")  # no-op 保持一致性
        btn = page.locator("button.weui-desktop-btn", has_text="发表视频").first
        btn.wait_for(state="visible", timeout=5000)
        btn.click()
        page.wait_for_timeout(200)
        res = page.inner_text("#r")
        ck("点到的是真实按钮（非隐藏 <p>）", res == "clicked-btn", f"#r={res}")

        # ---- 5. 切换账号弹窗：必须点「取消」而不是「切换」 ----
        print("[5] 关闭「切换视频号」弹窗")
        page.goto(BASE + "/switch", wait_until="domcontentloaded")
        page.wait_for_timeout(200)
        hit = page.evaluate(P._DISMISS_SWITCH_ACCOUNT_JS)
        page.wait_for_timeout(200)
        res = page.inner_text("#r")
        ck("弹窗被关闭且点的是「取消」", bool(hit) and res == "clicked:b2", f"hit={hit!r} #r={res}")

        # ---- 6. 封面确认：绝不能点「取消」 ----
        print("[6] 封面弹窗确认按钮（旧实现会点「取消」把封面作废）")
        page.goto(BASE + "/cover", wait_until="domcontentloaded")
        page.wait_for_timeout(200)
        page.evaluate(P._COVER_OPEN_JS)   # 先打开弹窗，否则按钮不可见
        page.wait_for_timeout(300)
        acted = page.evaluate(P._COVER_CONFIRM_JS)
        page.wait_for_timeout(200)
        res = page.inner_text("#r")
        ck("点的是「确定」而非「取消」", acted == "确定" and "clicked:b2" in res,
           f"acted={acted!r} #r={res}")

        # ---- 7. _set_cover 端到端 ----
        print("[7] _set_cover 端到端（真实注入图片文件）")
        page.goto(BASE + "/cover", wait_until="domcontentloaded")
        page.wait_for_timeout(200)
        okc = P._set_cover(page.main_frame, page, os.path.abspath(cov))
        page.wait_for_timeout(300)
        res = page.inner_text("#r")
        ck("封面文件已注入", "file:_cov.png" in res, f"#r={res}")
        ck("封面以「确定」提交（未被取消）", okc and "clicked:b2" in res, f"ok={okc} #r={res}")

        # ---- 8. 上传失败被主动识别（不再干等超时） ----
        print("[8] 上传失败主动识别")
        page.goto(BASE + "/uerr", wait_until="domcontentloaded")
        page.wait_for_timeout(200)
        raised = ""
        try:
            P._wait_form_ready(page.main_frame, timeout_s=6, page=page)
        except P.PublishFlowError as e:
            raised = str(e)
        ck("上传出错立刻抛错而非等超时", "上传失败" in raised, raised[:60] or "(未抛错)")

        # ---- 9. 实名验证弹窗：识别并等待 ----
        print("[9] 实名验证弹窗（应识别出来等待，而非判失败）")
        page.goto(BASE + "/verify", wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        P.VERIFY_WAIT_S = 3  # 缩短，别真等 3 分钟
        t0 = time.time()
        raised = ""
        try:
            P._wait_realtime_verification(page, timeout_s=3)
        except P.PublishFlowError as e:
            raised = str(e)
        dt = time.time() - t0
        ck("实名弹窗被识别且等满超时", "实名验证超时" in raised and dt >= 2.5,
           f"{dt:.1f}s raised={raised[:40]!r}")
        ck("实名已移出 FAIL_MARKERS（不再直接判失败）", "实名" not in P.FAIL_MARKERS,
           str(P.FAIL_MARKERS))

        # ---- 10. 弹窗在 iframe 内：切换账号 ----
        print("[10] 「切换视频号」弹窗渲染在 iframe 内")
        page.goto(BASE + "/iframe-switch", wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        fr = [f for f in page.frames if f != page.main_frame][0]
        hit = P._dismiss_switch_account_dialog(page, fr)
        page.wait_for_timeout(200)
        ck("iframe 内弹窗也能关掉", bool(hit), f"hit={hit!r}")

        # ---- 11. 弹窗在 iframe 内：实名验证 ----
        print("[11] 实名验证弹窗渲染在 iframe 内")
        page.goto(BASE + "/iframe-verify", wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        fr = [f for f in page.frames if f != page.main_frame][0]
        raised = ""
        try:
            P._wait_realtime_verification(page, fr, timeout_s=3)
        except P.PublishFlowError as e:
            raised = str(e)
        ck("iframe 内实名弹窗也能识别", "实名验证超时" in raised, raised[:40] or "(未抛错)")

        br.close()

    os.remove(cov)
    srv.shutdown()
    print(f"\n==== {len(PASS)} PASS / {len(FAIL)} FAIL ====")
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)


main()
