"""通知模块：私信/评论通知是本项目核心。

通知渠道（按 NOTIFY_CHANNEL 选择）：
  1. 控制台打印（始终）
  2. 写入本地日志文件 state/notifications.log（始终，永不丢失）
  3. 推送渠道：
     - wecom_app ：企业微信「应用消息」（官方 API，直推企业微信 App；
                   在企业微信中开启“微信插件”后可同时转发到个人微信）
     - webhook   ：企业微信 / 钉钉「群机器人」webhook
                   （企业微信 v4.1.41 起「群机器人」已更名为「消息推送」，
                     入口在内部群 → 右上角「…」→ 消息推送；外部群无此入口）
     - serverchan：Server酱，直接推到你的【个人微信】（绑定的服务号）
     - pushplus  ：PushPlus，直接推到你的【个人微信】

所有渠道参数都支持「每账号覆盖」：账号配置里的 notify 字段传同名参数即可。
"""
import os
import time

import requests

from . import config

# 企业微信 access_token 缓存（官方 token 有效期 7200 秒）
_wecom_token_cache = {"key": None, "token": None, "expire": 0.0}


CHANNEL_LABEL = {
    "wecom_app": "企业微信应用消息",
    "webhook": "企业微信「消息推送」（原群机器人）Webhook",
    "serverchan": "Server酱（个人微信）",
    "pushplus": "PushPlus（个人微信）",
    "": "仅本地日志（不推送）",
}

# 各渠道需要哪些参数才算「配置完整」
CHANNEL_NEEDS = {
    "wecom_app": (("WECOM_CORPID", "corpid"), ("WECOM_SECRET", "secret"), ("WECOM_AGENTID", "agentid")),
    "webhook": (("NOTIFY_WEBHOOK", "webhook 地址"),),
    "serverchan": (("SERVERCHAN_SENDKEY", "SendKey"),),
    "pushplus": (("PUSHPLUS_TOKEN", "token"),),
}


def channel_status() -> dict:
    """自检当前通知渠道是否真的能推送出去。

    常见问题：NOTIFY_CHANNEL 填了 serverchan，但 SERVERCHAN_SENDKEY 是空的
    —— 此时通知只会写进本地日志，一条都发不到微信，而且没有任何报错。
    这个函数把这种情况显式暴露出来，供面板显示。
    """
    ch = (config.NOTIFY_CHANNEL or "").lower()
    label = CHANNEL_LABEL.get(ch, f"未知渠道（{ch}）")
    needs = CHANNEL_NEEDS.get(ch, ())
    missing = [name for key, name in needs if not getattr(config, key, "")]
    if ch not in CHANNEL_NEEDS:
        return {"channel": ch, "label": label, "ok": False,
                "tip": f"NOTIFY_CHANNEL={ch} 不是已知渠道，通知只会写本地日志", "missing": []}
    return {
        "channel": ch,
        "label": label,
        "ok": not missing,
        "missing": missing,
        "tip": "" if not missing else f"缺少 {'、'.join(missing)}，通知只写日志、推不出去",
    }


def _with_retry(fn, name: str, tries: int = 3, wait_s: float = 2.0):
    """统一重试包装。

    推送失败绝大多数是瞬时问题（网络抖动、对端 5xx、限流），
    单次发送会让一条私信/评论通知直接丢失——而通知是这个项目的核心产出。
    采用「短间隔指数退避」，总耗时可控（约 6 秒内完成 3 次）。
    """
    last = None
    for i in range(1, tries + 1):
        try:
            fn()
            return True
        except Exception as e:
            last = e
            print(f"[WARN] {name} 推送失败（第 {i}/{tries} 次）：{str(e)[:100]}")
            if i < tries:
                time.sleep(wait_s * i)
    print(f"[ERROR] {name} 推送连续 {tries} 次失败，本条通知仅存于本地日志")
    return False


def notify(
    title: str,
    message: str,
    *,
    channel: str | None = None,
    webhook: str | None = None,
    webhook_type: str | None = None,
    serverchan_sendkey: str | None = None,
    pushplus_token: str | None = None,
    wecom_corpid: str | None = None,
    wecom_secret: str | None = None,
    wecom_agentid: str | int | None = None,
    wecom_touser: str | None = None,
) -> None:
    """发送一条通知。

    参数支持「每账号覆盖」：传入具体渠道参数时优先使用，否则回落到全局 config。
    这用于多账号时，不同视频号推到不同的接收人/渠道。
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {title} | {message}"

    # 1. 控制台
    print(line)

    # 2. 本地日志
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(config.NOTIFY_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # 日志失败不应阻断主流程
        print(f"[WARN] 写通知日志失败: {e}")

    # 3. 渠道分发（优先用传入覆盖，否则用全局）
    ch = (channel or config.NOTIFY_CHANNEL).lower()

    if ch == "wecom_app":
        corpid = wecom_corpid or config.WECOM_CORPID
        secret = wecom_secret or config.WECOM_SECRET
        agentid = wecom_agentid or config.WECOM_AGENTID
        touser = wecom_touser or config.WECOM_TOUSER
        if corpid and secret and agentid:
            _with_retry(
                lambda: _send_wecom_app(title, message, corpid, secret, agentid, touser),
                "企业微信应用消息",
            )
        else:
            print("[WARN] wecom_app 渠道缺少 WECOM_CORPID/SECRET/AGENTID 配置，跳过推送")
        return

    if ch == "webhook" and (webhook or config.NOTIFY_WEBHOOK):
        _with_retry(
            lambda: _send_webhook(
                title, message,
                webhook or config.NOTIFY_WEBHOOK,
                webhook_type or config.NOTIFY_WEBHOOK_TYPE,
            ),
            "Webhook",
        )
    elif ch == "serverchan" and (serverchan_sendkey or config.SERVERCHAN_SENDKEY):
        _with_retry(
            lambda: _send_serverchan(title, message, serverchan_sendkey or config.SERVERCHAN_SENDKEY),
            "Server酱",
        )
    elif ch == "pushplus" and (pushplus_token or config.PUSHPLUS_TOKEN):
        _with_retry(
            lambda: _send_pushplus(title, message, pushplus_token or config.PUSHPLUS_TOKEN),
            "PushPlus",
        )
    elif ch not in ("", "none"):
        # 渠道选了但凭据没配：明确说出来，否则用户以为通知发出去了
        print(f"[WARN] 通知渠道「{CHANNEL_LABEL.get(ch, ch)}」缺少凭据，本条仅写入本地日志")


# ---------------------------------------------------------------------------
# 各渠道实现
# ---------------------------------------------------------------------------
def _get_wecom_token(corpid: str, secret: str) -> str:
    """获取（带缓存）企业微信全局 access_token。"""
    now = time.time()
    key = f"{corpid}:{secret[:8]}"
    if _wecom_token_cache["token"] and _wecom_token_cache["key"] == key and _wecom_token_cache["expire"] > now + 60:
        return _wecom_token_cache["token"]
    r = requests.get(
        "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
        params={"corpid": corpid, "corpsecret": secret},
        timeout=10,
    )
    data = r.json()
    if data.get("errcode") == 0:
        _wecom_token_cache.update(
            key=key, token=data["access_token"], expire=now + data.get("expires_in", 7200)
        )
        return data["access_token"]
    raise RuntimeError(f"获取企业微信 access_token 失败: {data}")


def _send_wecom_app(title, message, corpid, secret, agentid, touser) -> None:
    """企业微信「应用消息」：官方接口，推到成员的企业微信（可转发个人微信）。"""
    token = _get_wecom_token(corpid, secret)
    payload = {
        "touser": touser or "@all",
        "msgtype": "text",
        "agentid": int(agentid),
        "text": {"content": f"{title}\n{message}"},
    }
    r = requests.post(
        "https://qyapi.weixin.qq.com/cgi-bin/message/send",
        params={"access_token": token},
        json=payload,
        timeout=10,
    )
    data = r.json()
    if data.get("errcode") != 0:
        # token 过期是最常见原因，清缓存后由外层重试重新获取
        if data.get("errcode") in (40001, 40014, 42001):
            _wecom_token_cache.update(key=None, token=None, expire=0.0)
        raise RuntimeError(f"企业微信返回错误: {data}")
    print("[INFO] 企业微信应用消息推送成功")


def _send_webhook(title: str, message: str, webhook: str, webhook_type: str) -> None:
    if webhook_type == "dingtalk":
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": f"## {title}\n\n{message}"},
        }
    elif webhook_type == "wecom":
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{message}"}}
    else:
        payload = {"text": f"{title}\n{message}"}
    r = requests.post(webhook, json=payload, timeout=10)
    # 群机器人正常返回 200 且 body 为空；非 2xx 直接视为失败交给重试
    r.raise_for_status()
    print("[INFO] Webhook 推送成功")


def _send_serverchan(title: str, message: str, sendkey: str) -> None:
    """Server酱：消息直接到个人微信（绑定的服务号）。

    ⚠️ 免费版每天只有 5 条配额——多账号轮询一圈就会用尽，
    建议改用 wecom_app（配额宽）或 PushPlus（200 条/天）。
    """
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    r = requests.post(url, data={"title": title, "desp": message}, timeout=10)
    data = r.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(f"Server酱返回错误: {data}")
    print("[INFO] Server酱推送成功")


def _send_pushplus(title: str, message: str, token: str) -> None:
    """PushPlus：消息直接到个人微信。"""
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": title,
        "content": message,
        "template": "txt",
    }
    r = requests.post(url, json=payload, timeout=10)
    data = r.json()
    if data.get("code") != 200:
        raise RuntimeError(f"PushPlus 返回错误: {data}")
    print("[INFO] PushPlus 推送成功")
