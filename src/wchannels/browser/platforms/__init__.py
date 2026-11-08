"""平台适配器注册表与统一发布入口。"""
from ._base import PublishInput, PublishOutcome, Publisher
from .wechat import WeChatPublisher
from .douyin import DouyinPublisher
from .xiaohongshu import XiaohongshuPublisher

_REGISTRY: dict[str, type[Publisher]] = {
    "wechat": WeChatPublisher,
    "douyin": DouyinPublisher,
    "xiaohongshu": XiaohongshuPublisher,
}


def get_publisher(platform: str) -> Publisher:
    cls = _REGISTRY.get(platform, WeChatPublisher)
    return cls()


def publish_for_account(
    ctx,
    account,
    *,
    video_path: str,
    short_title: str = "",
    description: str = "",
    topics: str = "",
    ai_disclosure: str = "",
    collection: str = "",
    cover_path: str = "",
    scheduled_at=None,
    original_work: bool = False,
    visibility: str = "public",
) -> tuple[bool, str, str]:
    """按账号所属平台选适配器，执行一次发布。返回 (是否成功, 消息, 截图路径)。"""
    pub = get_publisher(getattr(account, "platform", "wechat"))
    inp = PublishInput(
        video_path=video_path,
        short_title=short_title,
        description=description,
        topics=topics,
        ai_disclosure=ai_disclosure,
        collection=collection,
        cover_path=cover_path,
        scheduled_at=scheduled_at,
        original_work=original_work,
        visibility=visibility,
    )
    out = pub.publish(ctx, inp)
    return out.ok, out.message, out.screenshot


def collect_for_account(ctx, account) -> tuple[list[dict], str]:
    """按账号平台读取作品列表与数据。返回 (作品列表, 错误信息)。

    小红书等尚未实现的平台返回空列表 + 说明，不视为异常。
    """
    pub = get_publisher(getattr(account, "platform", "wechat"))
    fn = getattr(pub, "collect_works", None)
    if not fn:
        return [], f"{account.platform} 暂未支持作品数据采集"
    try:
        return fn(ctx)
    except Exception as e:
        return [], f"采集失败：{e}"


def collect_comments_for_account(ctx, account, works: list[dict]):
    """按账号平台读取评论。

    works 是「本账号已有作品的标题列表」，评论页按作品分组，需要先点开作品。
    返回 (按 work_id 分组的 {wid: (work, items)}, 错误信息)。
    """
    pub = get_publisher(getattr(account, "platform", "wechat"))
    fn = getattr(pub, "collect_comments", None)
    if not fn:
        return {}, f"{account.platform} 暂未支持评论采集"
    try:
        return fn(ctx, works)
    except Exception as e:
        return {}, f"评论采集失败：{e}"
