"""数据看板：按账号区分的发布数据可视化。

页面提供 KPI、账号维度、趋势、失败分布、时段分布与明细表。
图表用 ECharts（本地静态文件，零构建链、不依赖 CDN）。
"""
import csv
import io
import time

from flask import Response, render_template, request

from .. import accounts, stats
from .base import app


@app.route("/dashboard")
def dashboard():
    days = min(365, max(1, int(request.args.get("days", "30"))))
    account_id = request.args.get("account", "")
    data = stats.build(days=days, account_id=account_id)
    return render_template(
        "dashboard.html",
        d=data,
        days=days,
        account_id=account_id,
        accounts=accounts.load_accounts(),
        title="数据看板",
    )


@app.route("/dashboard/export.csv")
def dashboard_export():
    """导出 CSV。**必须加utf-8-sig BOM**，否则 Excel 打开中文乱码。"""
    days = min(365, max(1, int(request.args.get("days", "30"))))
    account_id = request.args.get("account", "")
    data = stats.build(days=days, account_id=account_id)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "批次ID", "发布时间", "账号", "素材标题", "状态", "耗时(秒)",
        "错误码", "错误原因", "失败阶段", "详情", "成功判定口径",
    ])
    for r in data["rows"]:
        w.writerow([
            r["batch_id"], r["requested_at"], r["account_name"],
            r["material_title"], r["status_label"],
            round(r["duration_ms"] / 1000, 1),
            r["error_code"], r["error_label"], r["error_stage"],
            (r["text"] or "")[:200],
            "跳转内容管理页",
        ])
    out = buf.getvalue()
    fn = f"发布记录_{time.strftime('%Y%m%d_%H%M')}.csv"
    # BOM 让Excel 正确识别 UTF-8（否则中文全是乱码）
    return Response(
        "﻿" + out,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{fn}",
            #手工指定 charset：Flask 会给 text/csv 自动补 charset，导致重复
            "Content-Type": "text/csv; charset=utf-8",
        },
    )


@app.route("/dashboard/ledger.json")
def dashboard_json():
    """给前端筛选/联动的原始数据接口。"""
    days = min(365, max(1, int(request.args.get("days", "30"))))
    account_id = request.args.get("account", "")
    return stats.build(days=days, account_id=account_id)