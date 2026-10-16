"""一次性数据迁移：把旧 publish_history 转换成 ledger 的 batch/target 结构。

背景：老版本把发布记录存在 `materials.json > publish_history`，只有时间/账号/成功与否，
既无法跨素材聚合，也会随素材删除而丢失。新版 ledger 结构更完整，
但**已有历史不会自动出现**——本模块负责补上这一步。

幂等：按 batch_id 去重，重复执行不会产生重复数据。
"""
import time

from . import ledger, materials


def migrate_legacy(days: int = 0) -> dict:
    """扫描所有素材的 publish_history，生成对应的 batch/target。

    返回 {"batches_created": n, "records": n, "skipped": n}
    """
    items = materials.load_materials()
    existing = {b.get("batch_id") for b in ledger.load_batches()}

    # 把历史记录按 (素材, 时间) 分组成 batch：
    # 同一时间点发往多个账号的记录会归为一个 batch。
    created = 0
    total_rec = 0
    skipped = 0

    for m in items:
        hist = m.get("publish_history") or []
        if not hist:
            continue
        # 按时间聚类：时间戳相同（精确到秒）或相差 <5 秒的视为同一次批量发布
        groups = []
        for h in hist:
            ts = h.get("time", "")
            if groups and _same_batch(ts, groups[-1][0].get("time", "")):
                groups[-1].append(h)
            else:
                groups.append([h])

        for grp in groups:
            ts = grp[0].get("time", "")
            # 生成稳定 batch_id：同样的输入重复执行得到同样的 id → 天然幂等
            bid = "b_legacy_" + _slug(ts) + "_" + _slug(m.get("id", ""))
            if bid in existing:
                skipped += len(grp)
                continue

            targets = []
            for h in grp:
                ok = bool(h.get("ok"))
                txt = h.get("result", "") or ""
                # 老记录无法区分「失败」与「未确认」，保守归为 failed
                # （宁可统计偏保守，也不要虚报成功率）
                st = "success" if ok else "failed"
                code, stage = ('', '') if ok else ledger.classify_error(txt)
                targets.append({
                    "target_id": "t_" + _slug(ts) + "_" + h.get("account_id", ""),
                    "account_id": h.get("account_id", ""),
                    "account_name": h.get("account_name", ""),
                    "status": st,
                    "success_criterion": ledger.SUCCESS_CRITERION,
                    "queued_at": ts,
                    "started_at": ts,
                    "finished_at": ts,
                    # 老记录没有耗时，置 0 而不是编造
                    "duration_ms": 0,
                    "error_code": code,
                    "error_message": txt[:200],
                    "error_stage": stage,
                    "attempt": 1,
                    "retry_of": "",
                    "screenshot": "",
                    "result_text": txt[:200],
                    "_legacy": True,
                })

            succ = sum(1 for t in targets if t["status"] == "success")
            batch = {
                "batch_id": bid,
                "material_id": m.get("id", ""),
                "material_title": m.get("title", ""),
                "trigger": "legacy_import",
                "requested_at": ts,
                "requested_ts": _ts(ts),
                "scheduled_at": m.get("scheduled_at", ""),
                "config_snapshot": {
                    "short_title": m.get("short_title", ""),
                    "ai_declare": m.get("ai_declare", ""),
                    "visibility": m.get("visibility", "公开"),
                    "scheduled_at": m.get("scheduled_at", ""),
                },
                "targets": targets,
                "status": ("success" if succ == len(targets)
                           else ("partial_success" if succ else "failed")),
                "finished_at": ts,
                "_legacy": True,
            }
            bs = ledger.load_batches()
            bs.append(batch)
            ledger.save_batches(bs)
            existing.add(bid)
            created += 1
            total_rec += len(targets)

    return {"batches_created": created, "records": total_rec, "skipped": skipped}


def _same_batch(t1: str, t2: str, window_s: int = 5) -> bool:
    a, b = _ts(t1), _ts(t2)
    return a > 0 and b > 0 and abs(a - b) <= window_s


def _ts(s: str) -> float:
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def _slug(s: str) -> str:
    """把时间/ID 转成文件名安全的短串。"""
    out = "".join(c if c.isalnum() else "_" for c in str(s))
    return out[:24]