"""MCP 接口（stdio）：把工作台的核心能力包成 MCP tools，供 AI 助手直接调用。

用法：python -m wchannels.mcp_server
在 MCP 客户端（如 Trae/Claude Desktop）里配置该命令即可。
协议：MCP 2024-11-05，stdio + 换行分隔的 JSON-RPC 2.0，无第三方依赖。
"""
import json
import sys

from sqlmodel import Session, select

from .db import get_engine
from .models import Account, Comment, Material, PublishTarget, WorkStat

PROTOCOL = "2024-11-05"
SERVER = {"name": "wchannels", "version": "0.1.0"}

TOOLS = [
    {"name": "list_accounts",
     "description": "列出工作台上的平台账号（名称/平台/状态/分组）",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_comments",
     "description": "列出采集到的评论。status 可筛 new/drafted/replied/ignored",
     "inputSchema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["", "new", "drafted", "replied", "ignored"]},
         "account_id": {"type": "integer"},
         "limit": {"type": "integer", "default": 30}}}},
    {"name": "draft_reply",
     "description": "为指定评论生成 AI 回复草稿（写回该评论的草稿字段）",
     "inputSchema": {"type": "object", "required": ["comment_id"],
                     "properties": {"comment_id": {"type": "integer"}}}},
    {"name": "send_reply",
     "description": "一键回复：把文本直接发到平台上对应评论下（约 20~40 秒，不可逆）",
     "inputSchema": {"type": "object", "required": ["comment_id", "text"],
                     "properties": {"comment_id": {"type": "integer"},
                                    "text": {"type": "string"}}}},
    {"name": "collect_comments",
     "description": "对所有 active 账号立即采集一轮新评论（较慢，每账号约 30~60 秒）",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_messages",
     "description": "列出采集到的私信会话（发送者/最后一条/未读数）。only_unread 只看有未读的",
     "inputSchema": {"type": "object", "properties": {
         "account_id": {"type": "integer"},
         "only_unread": {"type": "boolean", "default": False},
         "limit": {"type": "integer", "default": 30}}}},
    {"name": "collect_messages",
     "description": "对所有 active 账号立即采集一轮新私信（较慢，每账号约 30~60 秒）",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_works",
     "description": "列出作品数据（标题/播放/点赞/评论数）",
     "inputSchema": {"type": "object", "properties": {
         "account_id": {"type": "integer"},
         "limit": {"type": "integer", "default": 20}}}},
    {"name": "list_schedule",
     "description": "列出待发布排期（排队中/等到点的任务，按预定时间升序）",
     "inputSchema": {"type": "object", "properties": {
         "account_id": {"type": "integer"},
         "limit": {"type": "integer", "default": 30}}}},
    {"name": "list_publish_records",
     "description": "列出发布结果记录（成功/失败/跳过，最近在前）",
     "inputSchema": {"type": "object", "properties": {
         "account_id": {"type": "integer"},
         "status": {"type": "string", "enum": ["", "success", "failed", "skipped"]},
         "limit": {"type": "integer", "default": 30}}}},
    {"name": "draft_message",
     "description": "为指定私信生成 AI 回复草稿（写回该私信的草稿字段，不发送）",
     "inputSchema": {"type": "object", "required": ["message_id"],
                     "properties": {"message_id": {"type": "integer"}}}},
    {"name": "send_message",
     "description": "把文本直接发到平台私信会话（约 20~40 秒，不可逆；仅视频号支持，且该私信需带会话 ID）",
     "inputSchema": {"type": "object", "required": ["message_id", "text"],
                     "properties": {"message_id": {"type": "integer"},
                                    "text": {"type": "string"}}}},
    {"name": "create_publish",
     "description": "创建发布批次：选素材 × 账号自动错峰排期。创建后调度器会按排期自动发布（通常 30 秒内开始第一条），如需审核请先确认素材文案",
     "inputSchema": {"type": "object",
                     "required": ["material_ids", "account_ids"],
                     "properties": {
                         "material_ids": {"type": "array", "items": {"type": "integer"}},
                         "account_ids": {"type": "array", "items": {"type": "integer"}},
                         "stagger_min": {"type": "integer",
                                         "description": "相邻两条的错峰分钟数，留空用各平台默认"}}}},
    {"name": "list_materials",
     "description": "列出素材库（标题/状态/标签/时长）。status 可筛 inbox/ready/published/trash",
     "inputSchema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["", "inbox", "ready", "published", "trash"]},
         "limit": {"type": "integer", "default": 30}}}},
]


def _ok(data) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(data, ensure_ascii=False, default=str)}]}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def _call(name: str, args: dict) -> dict:
    from .core import comments as cmt
    from .models import User

    with Session(get_engine()) as s:
        user = s.exec(select(User)).first()  # 本地单用户工作台
        uid = user.id if user else 0
        if name == "list_accounts":
            rows = s.exec(select(Account)).all()
            return _ok([{"id": a.id, "name": a.name, "platform": a.platform,
                         "status": str(a.status), "group": a.group} for a in rows])

        if name == "list_comments":
            items = cmt.list_comments(s, uid, status=args.get("status") or "",
                                      account_id=args.get("account_id"),
                                      limit=int(args.get("limit") or 30))
            return _ok(items)

        if name == "draft_reply":
            c = s.get(Comment, int(args["comment_id"]))
            if not c:
                return _err("评论不存在")
            from .ai import reply as ai_reply
            acc = s.get(Account, c.account_id)
            c.ai_draft = ai_reply.draft(c.content, c.work_title,
                                        acc.name if acc else "")
            c.status = "drafted"
            s.add(c)
            s.commit()
            return _ok({"comment_id": c.id, "ai_draft": c.ai_draft})

        if name == "send_reply":
            ok, err = cmt.send_reply(s, int(args["comment_id"]),
                                     str(args.get("text") or ""))
            return _ok({"ok": True}) if ok else _err(err)

        if name == "collect_comments":
            results = cmt.collect_all(s, notify_new=True)
            return _ok(results)

        if name == "list_messages":
            from .core import messages as msg
            items = msg.list_messages(s, uid, account_id=args.get("account_id"),
                                      only_unread=bool(args.get("only_unread")),
                                      limit=int(args.get("limit") or 30))
            return _ok(items)

        if name == "collect_messages":
            from .core import messages as msg
            results = msg.collect_all(s, notify_new=True)
            return _ok(results)

        if name == "list_works":
            q = select(WorkStat).order_by(WorkStat.id.desc())
            if args.get("account_id"):
                q = q.where(WorkStat.account_id == int(args["account_id"]))
            rows = s.exec(q.limit(int(args.get("limit") or 20))).all()
            return _ok([{"title": w.work_title, "views": w.views,
                         "likes": w.likes, "comments": w.comments,
                         "published_at": str(w.published_at)} for w in rows])

        if name in ("list_schedule", "list_publish_records"):
            q = select(PublishTarget)
            if name == "list_schedule":
                q = q.where(PublishTarget.status.in_(["queued", "wait_time"]))
                q = q.order_by(PublishTarget.scheduled_at.asc())
            else:
                st = args.get("status") or ""
                q = q.where(PublishTarget.status.in_([st] if st else ["success", "failed", "skipped"]))
                q = q.order_by(PublishTarget.id.desc())
            if args.get("account_id"):
                q = q.where(PublishTarget.account_id == int(args["account_id"]))
            rows = s.exec(q.limit(int(args.get("limit") or 30))).all()
            out = []
            for t in rows:
                m = s.get(Material, t.material_id)
                a = s.get(Account, t.account_id)
                out.append({
                    "id": t.id, "material": m.title if m else "（素材已删除）",
                    "account": a.name if a else "", "platform": a.platform if a else "",
                    "status": t.status,
                    "scheduled_at": str(t.scheduled_at) if t.scheduled_at else None,
                    "result_message": t.result_message,
                    "published_at": str(t.published_at) if t.published_at else None,
                })
            return _ok(out)

        if name == "draft_message":
            from .core import messages as msg_svc
            from .models import PrivateMessage
            m = s.get(PrivateMessage, int(args["message_id"]))
            if not m or m.user_id != uid:
                return _err("私信不存在")
            from .ai import reply as ai_reply
            acc = s.get(Account, m.account_id)
            try:
                import json as _json
                history = _json.loads(m.history) if getattr(m, "history", "") else None
            except Exception:
                history = None
            try:
                m.ai_draft = ai_reply.draft_dm(m.sender, m.content,
                                               acc.name if acc else "",
                                               history=history)
            except Exception as e:
                return _err(f"生成回复草稿失败：{e}")
            s.add(m)
            s.commit()
            return _ok({"message_id": m.id, "ai_draft": m.ai_draft})

        if name == "send_message":
            from .core import messages as msg_svc
            ok, err = msg_svc.send_reply(s, int(args["message_id"]),
                                         str(args.get("text") or ""), user_id=uid)
            return _ok({"ok": True}) if ok else _err(err)

        if name == "create_publish":
            from .core import queue as queue_svc
            try:
                b = queue_svc.create_batch(
                    s, uid,
                    [int(x) for x in args.get("material_ids") or []],
                    [int(x) for x in args.get("account_ids") or []],
                    args.get("stagger_min"),
                )
            except queue_svc.OwnershipError as e:
                return _err(str(e))
            return _ok({"batch_id": b.id, "status": b.status,
                        "note": "已排入队列，调度器会按排期自动发布"})

        if name == "list_materials":
            q = select(Material).where(Material.user_id == uid)
            st = args.get("status") or ""
            if st:
                q = q.where(Material.status == st)
            rows = s.exec(q.order_by(Material.id.desc())
                          .limit(int(args.get("limit") or 30))).all()
            return _ok([{"id": m.id, "title": m.title, "status": m.status,
                         "tags": m.tags, "duration_sec": m.duration_sec,
                         "short_title": m.short_title,
                         "created_at": str(m.created_at)} for m in rows])

    return _err(f"未知工具：{name}")


def _handle(msg: dict):
    mid = msg.get("id")
    method = msg.get("method", "")
    try:
        if method == "initialize":
            return {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                    "serverInfo": SERVER}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            p = msg.get("params") or {}
            return _call(p.get("name", ""), p.get("arguments") or {})
        if method.startswith("notifications/"):
            return None  # 通知无需响应
        return {"error": {"code": -32601, "message": f"method not found: {method}"}}
    except Exception as e:
        return {"error": {"code": -32000, "message": str(e)}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = _handle(msg)
        if result is None or "id" not in msg:
            continue
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": msg["id"], "result": result},
            ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
