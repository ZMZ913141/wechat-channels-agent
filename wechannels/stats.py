"""发布统计聚合 —— 把 ledger 的原始记录算成看板指标。

单独成模块的理由：聚合口径是最容易出错的地方（分母含不含 skipped？
按天切分用本地时区还是 UTC？），集中一处便于校验和写测试。

## 关键口径（不要随手改，会影响历史数据可比性）
1. **成功率分母 = success + failed**，**排除** `unverified` 与 `skipped`。
   - unverified：已提交但未能确认，不等于失败
   - skipped：被跳过（如账号停用），不是失败
2. **按天切分用本地日期**，不用 UTC——否则早上 8 点前的发布会被算到前一天。
3. **耗时 duration_ms** = target 的实际执行耗时，不含排队等待。
4. **贡献率** = 该账号 target 数 / 全部账号 target 数。
"""
import time
from collections import Counter, defaultdict

from . import accounts, ledger

# 8 色定性色板（categorical palette）。按账号创建顺序固定分配，永不随数据变化。
# 关键：颜色是账号的持久标识，不是状态色——状态另有语义色（绿/红/灰）。
PALETTE = [
    "#3b82f6",  # 蓝
    "#f59e0b",  # 琥珀
    "#10b981",  # 翠绿
    "#ef4444",  # 红
    "#a855f7",  # 紫
    "#06b6d4",  # 青
    "#ec4899",  # 粉
    "#84cc16",  # 柠绿
]

STATUS_LABEL = {
    "success": "成功",
    "failed": "失败",
    "unverified": "未确认",
    "skipped": "已跳过",
    "pending": "排队中",
    "running": "发布中",
}


def account_color_map() -> dict:
    """账号 id → 固定颜色。按账号在列表中的序号分配，顺序稳定。"""
    out = {}
    for i, a in enumerate(accounts.load_accounts()):
        out[a["id"]] = PALETTE[i % len(PALETTE)]
    return out


def account_identity_map() -> dict:
    """账号 id → 辨识信息（可信昵称 / 你的命名 / 可信度）。

    这是「按账号区分」的关键：图例与卡片里显示的是**实测昵称**，
    而不是你自己起的标签——「视频号 2」这种名字无法区分真实身份。
    """
    from . import loginprobe

    out = {}
    for a in accounts.load_accounts():
        d = loginprobe.display_name(a)
        out[a["id"]] = {
            "primary": d["primary"],# 可信昵称优先，否则你的命名
            "secondary": d.get("secondary", ""),
            "tone": d["tone"],
            "verified": d["verified"],
            "label": a.get("name") or a["id"],
        }
    return out


def _pct(a: int, b: int) -> float:
    return round(a * 100.0 / b, 1) if b else 0.0


def _day(ts: str) -> str:
    """从 'YYYY-MM-DD HH:MM:SS' 取日期部分（本地时区，勿转 UTC）。"""
    return (ts or "")[:10]


def _hour(ts: str) -> int:
    try:
        return int(ts[11:13])
    except Exception:
        return 0


def build(days: int = 30, account_id: str = "") -> dict:
    """聚合出看板所需的全部数据。返回的 dict 直接内联进模板。"""
    colors = account_color_map()
    ident = account_identity_map()
    accs = accounts.load_accounts()

    rows = []          # 每条 target 一行（明细表）
    by_acc = defaultdict(list)
    for b, t in ledger.iter_targets(days=days, account_id=account_id):
        aid = t.get("account_id", "")
        row = {
            "batch_id": b.get("batch_id", ""),
            "material_id": b.get("material_id", ""),
            "material_title": b.get("material_title", ""),
            "requested_at": b.get("requested_at", ""),
            "account_id": aid,
            # 优先用实测昵称（辨识度最高），label 是你自己的命名
            "account_name": ident.get(aid, {}).get("primary") or _acc_name(accs, aid),
            "account_label": ident.get(aid, {}).get("label") or _acc_name(accs, aid),
            "account_tone": ident.get(aid, {}).get("tone", "warn"),
            "account_verified": ident.get(aid, {}).get("verified", False),
            "color": colors.get(aid, "#9aa3b2"),
            "status": t.get("status", ""),
            "status_label": STATUS_LABEL.get(t.get("status", ""), t.get("status", "")),
            "duration_ms": int(t.get("duration_ms") or 0),
            "error_code": t.get("error_code", ""),
            "error_label": ledger.ERROR_CODES.get(t.get("error_code", ""), ""),
            "error_stage": t.get("error_stage", ""),
            "text": t.get("result_text") or t.get("error_message") or "",
            "day": _day(b.get("requested_at", "")),
            "hour": _hour(b.get("requested_at", "")),
        }
        rows.append(row)
        by_acc[aid].append(row)

    rows.sort(key=lambda r: r["requested_at"], reverse=True)

    # ---------- KPI ----------
    total_targets = len(rows)
    n_success = sum(1 for r in rows if r["status"] == "success")
    n_failed = sum(1 for r in rows if r["status"] == "failed")
    n_unver = sum(1 for r in rows if r["status"] == "unverified")
    n_skip = sum(1 for r in rows if r["status"] == "skipped")
    # 口径：分母只算 success + failed
    denom = n_success + n_failed
    durations = sorted(r["duration_ms"] for r in rows if r["duration_ms"] > 0)

    kpi = {
        "batches": len({r["batch_id"] for r in rows}),
        "targets": total_targets,
        "success": n_success,
        "failed": n_failed,
        "unverified": n_unver,
        "skipped": n_skip,
        "success_rate": _pct(n_success, denom),
        "p50_ms": _percentile(durations, 0.5),
        "p90_ms": _percentile(durations, 0.9),
        "avg_ms": int(sum(durations) / len(durations)) if durations else 0,
    }

    # ---------- 账号维度 ----------
    acct_stats = []
    for a in accs:
        aid = a["id"]
        rs = by_acc.get(aid, [])
        if account_id and aid != account_id:
            continue
        s = sum(1 for r in rs if r["status"] == "success")
        f = sum(1 for r in rs if r["status"] == "failed")
        u = sum(1 for r in rs if r["status"] == "unverified")
        ds = sorted(r["duration_ms"] for r in rs if r["duration_ms"] > 0)
        acct_stats.append({
            "account_id": aid,
            # 主显示名= 实测昵称（可信时），否则退回你的命名
            "name": ident.get(aid, {}).get("primary") or (a.get("name") or aid),
            "label": ident.get(aid, {}).get("label") or (a.get("name") or aid),
            "note": ident.get(aid, {}).get("secondary", ""),
            "tone": ident.get(aid, {}).get("tone", "warn"),
            "verified": ident.get(aid, {}).get("verified", False),
            "color": colors.get(aid, "#9aa3b2"),
            "total": len(rs),
            "success": s,
            "failed": f,
            "unverified": u,
            "success_rate": _pct(s, s + f),
            "p50_ms": _percentile(ds, 0.5),
            "avg_ms": int(sum(ds) / len(ds)) if ds else 0,
            "share": _pct(len(rs), total_targets),
            # 最近失败时间：用于「该号健康度」预警
            "last_failed": next((r["requested_at"] for r in rs if r["status"] == "failed"), ""),
            "last_ok": next((r["requested_at"] for r in rs if r["status"] == "success"), ""),
        })

    # ---------- 按天趋势 ----------
    days_list = sorted({r["day"] for r in rows if r["day"]})
    trend = []
    for d in days_list:
        day_rows = [r for r in rows if r["day"] == d]
        s = sum(1 for r in day_rows if r["status"] == "success")
        f = sum(1 for r in day_rows if r["status"] == "failed")
        trend.append({
            "day": d,
            "total": len(day_rows),
            "success_rate": _pct(s, s + f),
            # 按账号拆分，供堆叠柱状图使用
            "by_acc": {aid: sum(1 for r in day_rows if r["account_id"] == aid)
                        for aid in by_acc},
        })

    # ---------- 失败原因分布 ----------
    err = Counter(r["error_code"] for r in rows if r["status"] == "failed" and r["error_code"])
    error_dist = [{
        "code": code,
        "label": ledger.ERROR_CODES.get(code, code),
        "count": cnt,
        "by_acc": {aid: sum(1 for r in rows
                            if r["status"] == "failed" and r["error_code"] == code
                            and r["account_id"] == aid)
                   for aid in by_acc},
    } for code, cnt in err.most_common()]

    # ---------- 时段分布（0-23 时）----------
    hour_dist = [{"hour": h,
                  "total": sum(1 for r in rows if r["hour"] == h),
                  "failed": sum(1 for r in rows if r["hour"] == h and r["status"] == "failed")}
                 for h in range(24)]

    # ---------- 一稿多发覆盖率：同一素材覆盖了几个号 ----------
    mat_map = defaultdict(set)
    for r in rows:
        if r["status"] == "success":
            mat_map[r["material_id"]].add(r["account_id"])
    coverage = [
        {"material_id": mid, "title": next((r["material_title"] for r in rows
                                          if r["material_id"] == mid), ""),
         "accounts": len(aids)}
        for mid, aids in mat_map.items()
    ]
    coverage.sort(key=lambda x: -x["accounts"])

    return {
        "days": days,
        "kpi": kpi,
        "accounts": acct_stats,
        "trend": trend,
        "error_dist": error_dist,
        "hour_dist": hour_dist,
        "coverage": coverage[:20],
        "rows": rows[:300],         # 明细表最多300 行，避免页面过大
        "row_total": len(rows),
        "colors": colors,
        "identity": ident,
        "has_data": total_targets > 0,
    }


def _percentile(sorted_vals: list, q: float) -> int:
    """P50/P90。长尾往往意味着某账号被限流，比平均值更能说明问题。"""
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return sorted_vals[idx]


def _acc_name(accs: list, aid: str) -> str:
    for a in accs:
        if a["id"] == aid:
            return a.get("name") or aid
    return aid