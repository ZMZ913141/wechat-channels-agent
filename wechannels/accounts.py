"""多账号管理：每个视频号 = 一个独立的浏览器登录档案（user_data_dir）。

账号信息持久化在 state/accounts.json，结构：
[
  {
    "id": "default",            # 唯一标识
    "name": "默认视频号",        # 展示名（备注）
    "profile_dir": "profiles/default",  # 浏览器登录态目录（相对项目根或绝对）
    "enabled": true              # 是否启用（停用的账号不会出现在发布选项里）
  }
]

若 accounts.json 不存在，自动根据旧版 .env 的 USER_DATA_DIR 生成一个 default 账号，
保证向后兼容。
"""
import json
import os

from . import config

ACCOUNTS_FILE = os.path.join(config.STATE_DIR, "accounts.json")
CURRENT_FILE = os.path.join(config.STATE_DIR, "current_account.txt")

# ---------------------------------------------------------------------------
# 浏览器内核（channel）：让「不同账号 = 不同浏览器」成为可配置项。
# 每个账号本来就有独立档案（user_data_dir），登录态天然隔离；这里再让内核可分开，
# 就能做到「账号A 用 Chrome、账号B 用 Edge」——两个真正不同的浏览器，
# 可以同时打开、同时操作，互不干扰（只有**同一个账号**才不能同时开两个窗口）。
# ---------------------------------------------------------------------------
VALID_CHANNELS = ("", "chrome", "msedge", "chromium")
CHANNEL_LABELS = {
    "": "自动（本机 Chrome/Edge）",
    "chrome": "Google Chrome",
    "msedge": "Microsoft Edge",
    "chromium": "内置 Chromium（备选）",
}


def channel_of(account: dict) -> str:
    """账号指定的浏览器内核；''=自动（沿用全局 BROWSER_CHANNEL / 本机探测）。"""
    ch = ((account or {}).get("channel") or "").strip().lower()
    return ch if ch in VALID_CHANNELS else ""


def channel_label(account: dict) -> str:
    return CHANNEL_LABELS.get(channel_of(account), "自动")


def channel_candidates(pref: str) -> list:
    """把「账号偏好」展开成按优先级尝试的内核列表；'' 表示 Playwright 内置 Chromium。

    保留失败回退：用户给账号指定了 Chrome 但本机没装时，直接报错会让账号完全用不了；
    退到 Edge / 内置 Chromium 至少能打开窗口（启动日志会打印实际用的内核）。
    """
    pref = (pref or "").strip().lower()
    if pref == "chromium":
        return [""]
    if pref in ("chrome", "msedge"):
        other = "msedge" if pref == "chrome" else "chrome"
        return [pref, other, ""]
    cfg = (config.BROWSER_CHANNEL or "").strip().lower()
    if cfg == "chromium":
        return [""]
    if cfg in ("chrome", "msedge"):
        other = "msedge" if cfg == "chrome" else "chrome"
        return [cfg, other, ""]
    return ["chrome", "msedge", ""]


def _resolve(profile_dir: str) -> str:
    """把相对路径解析到项目根目录。"""
    if os.path.isabs(profile_dir):
        return profile_dir
    return os.path.abspath(os.path.join(config._BASE, profile_dir))


def _default_account() -> dict:
    """根据旧版配置生成一个默认账号。"""
    prof = config.USER_DATA_DIR
    # 转成相对项目根的路径，便于迁移
    try:
        rel = os.path.relpath(prof, config._BASE)
        prof = rel
    except Exception:
        pass
    return {
        "id": "default",
        "name": "默认视频号",
        "profile_dir": prof,
        "enabled": True,
    }


def _normalize_account(a: dict) -> dict:
    """补齐缺失/空白的字段。名称留空时回退显示 id（否则面板上是一行空白）。"""
    if not isinstance(a, dict):
        return a
    if not (a.get("name") or "").strip():
        a["name"] = a.get("id") or "未命名账号"
    if not (a.get("profile_dir") or "").strip():
        a["profile_dir"] = f"profiles/{a.get('id') or 'default'}"
    if ((a.get("channel") or "").strip().lower()) not in VALID_CHANNELS:
        a["channel"] = ""
    a.setdefault("enabled", True)
    return a


def load_accounts() -> list:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    if not os.path.exists(ACCOUNTS_FILE):
        accs = [_default_account()]
        save_accounts(accs)
        return accs
    try:
        with open(ACCOUNTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list) or not data:
            data = [_default_account()]
            save_accounts(data)
        return [_normalize_account(a) for a in data]
    except Exception:
        return [_default_account()]


def save_accounts(accounts: list) -> None:
    """落盘。**统一先 normalize**，避免再把空白名称/空档案路径写回磁盘。

    曾经踩过：账号 name 为空字符串 → 面板上显示成一行空白，
    且每次 load 才在内存里回退成 id、磁盘上一直是空的。
    """
    os.makedirs(config.STATE_DIR, exist_ok=True)
    data = [_normalize_account(dict(a)) if isinstance(a, dict) else a for a in accounts]
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_account(aid: str) -> dict | None:
    for a in load_accounts():
        if a["id"] == aid:
            return a
    return None


def get_enabled_accounts() -> list:
    return [a for a in load_accounts() if a.get("enabled", True)]


def current_account_id() -> str:
    try:
        with open(CURRENT_FILE, encoding="utf-8") as f:
            cid = f.read().strip()
        if cid and get_account(cid):
            return cid
    except Exception:
        pass
    accs = load_accounts()
    return accs[0]["id"] if accs else "default"


def set_current_account(aid: str) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(CURRENT_FILE, "w", encoding="utf-8") as f:
        f.write(aid)


def current_account() -> dict:
    return get_account(current_account_id()) or _default_account()


def profile_path(account: dict) -> str:
    """返回账号浏览器档案的绝对路径（不存在则创建父目录）。"""
    p = _resolve(account.get("profile_dir", "profiles/default"))
    os.makedirs(p, exist_ok=True)
    return p


def seen_path(account: dict) -> str:
    """返回账号的已读记录文件路径。"""
    return os.path.join(config.STATE_DIR, f"seen_{account['id']}.json")


def add_account(data: dict) -> dict:
    accs = load_accounts()
    aid = (data.get("id") or "").strip()
    if not aid:
        import uuid

        aid = uuid.uuid4().hex[:8]
    if any(a["id"] == aid for a in accs):
        raise ValueError(f"账号 id 已存在：{aid}")
    acc = _normalize_account({
        "id": aid,
        "name": data.get("name") or "",
        "profile_dir": data.get("profile_dir") or f"profiles/{aid}",
        "channel": data.get("channel") or "",
        "enabled": data.get("enabled", True),
    })
    accs.append(acc)
    save_accounts(accs)
    return acc


def update_account(aid: str, data: dict) -> dict | None:
    accs = load_accounts()
    for a in accs:
        if a["id"] == aid:
            for k in ("name", "profile_dir", "channel", "enabled"):
                if k in data:
                    a[k] = data[k]
            _normalize_account(a)
            save_accounts(accs)
            return a
    return None


def delete_account(aid: str) -> bool:
    accs = load_accounts()
    if aid == "default":
        return False  # 保护默认账号不被删除
    new = [a for a in accs if a["id"] != aid]
    if len(new) == len(accs):
        return False
    save_accounts(new)
    # 清理该账号的已读记录
    try:
        os.remove(os.path.join(config.STATE_DIR, f"seen_{aid}.json"))
    except Exception:
        pass
    if current_account_id() == aid:
        set_current_account(new[0]["id"])
    return True
