"""账号登录态检测：基于浏览器档案目录里 Cookies 文件的新鲜度判断「是否已登录」。

视频号登录态特征（官方说明）：扫码登录后每 4 小时需有一次操作维持、最多维持 24 小时。
因此 Cookies 文件 mtime 在 24 小时内视为「已登录（新鲜）」，超过则视为「可能已失效」。
这只是本地启发式（无法 100% 确认服务器端有效），用于在发布前给出友好提示，
真正的登录校验由浏览器打开页面后页面是否出现二维码决定。
"""
import os
import time

# 登录态视为有效的窗口（秒）：24 小时
FRESH_WINDOW = 24 * 3600


def _cookies_mtime(profile_dir: str) -> float | None:
    """返回 Cookies 文件最新 mtime，没有则 None。

    Chrome/Edge 的 user_data_dir 下通常还有一层 Default/ 子档案，
    所以要把 Default/Default、Default/Network 等组合都纳入候选。
    """
    bases = [profile_dir, os.path.join(profile_dir, "Default")]
    candidates = []
    for b in bases:
        candidates.append(os.path.join(b, "Cookies"))
        candidates.append(os.path.join(b, "Network", "Cookies"))
    best = None
    for c in candidates:
        if os.path.exists(c):
            t = os.path.getmtime(c)
            if best is None or t > best:
                best = t
    return best


def status(profile_dir: str) -> dict:
    """返回 {logged_in(bool), fresh(bool), age_hours(float|None), detail(str)}。"""
    ap = os.path.abspath(profile_dir)
    if not os.path.isdir(ap):
        return {"logged_in": False, "fresh": False, "age_hours": None, "detail": "档案不存在，需扫码登录"}
    mt = _cookies_mtime(ap)
    if mt is None:
        return {"logged_in": False, "fresh": False, "age_hours": None, "detail": "无登录 Cookie，需扫码登录"}
    age = time.time() - mt
    age_hours = age / 3600
    fresh = age < FRESH_WINDOW
    if fresh:
        detail = f"已登录（{age_hours:.1f} 小时前）"
    else:
        detail = f"登录可能已失效（{age_hours:.1f} 小时前，超过 24h）"
    return {"logged_in": True, "fresh": fresh, "age_hours": round(age_hours, 1), "detail": detail}
