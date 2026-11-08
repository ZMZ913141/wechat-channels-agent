"""发布流程统一入口（已重构为按平台适配器分发）。

保留 publish_one 作为兼容薄封装，新代码请直接用 platforms.publish_for_account。
"""
from .platforms import publish_for_account, PublishInput, PublishOutcome, Publisher, get_publisher


def publish_one(
    ctx,
    video_path: str,
    short_title: str = "",
    description: str = "",
    topics: str = "",
    ai_disclosure: str = "",
    scheduled_at=None,
    account=None,
    visibility: str = "public",
):
    """兼容旧调用：若传入 account 则按其平台发布，否则按视频号。"""
    platform = getattr(account, "platform", "wechat") if account is not None else "wechat"
    pub = get_publisher(platform)
    inp = PublishInput(
        video_path=video_path,
        short_title=short_title,
        description=description,
        topics=topics,
        ai_disclosure=ai_disclosure,
        scheduled_at=scheduled_at,
        visibility=visibility,
    )
    out = pub.publish(ctx, inp)
    return out.ok, out.message, out.screenshot


__all__ = [
    "publish_one", "publish_for_account", "PublishInput", "PublishOutcome",
    "Publisher", "get_publisher",
]
