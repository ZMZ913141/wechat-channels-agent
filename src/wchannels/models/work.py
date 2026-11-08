"""作品数据快照：发布后在平台侧的播放/互动数据。

为什么不直接写回 PublishTarget：数据每天都会变，需要按天留存才能看趋势；
PublishTarget 只保留「最后一次状态」，会被覆盖。
"""
from sqlmodel import SQLModel, Field
from datetime import datetime
from ..timeutils import now


class WorkStat(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    account_id: int = Field(index=True)
    material_id: int | None = Field(default=None, index=True)
    target_id: int | None = Field(default=None, index=True)

    platform: str = ""
    work_id: str = Field(default="", index=True)   # 平台作品 ID；拿不到时用内容指纹
    work_url: str = ""
    work_title: str = ""                            # 平台上的标题/描述前 80 字，便于人工核对
    published_at: datetime | None = None

    stat_date: str = Field(default="", index=True)  # YYYY-MM-DD（按采集日归档）
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    favorites: int = 0

    collected_at: datetime = Field(default_factory=now, index=True)


class Comment(SQLModel, table=True):
    """平台侧的观众评论。

    why 单独建表：作品数据是每天刷新的数字，评论是逐条内容，需要留档、
    标记是否已回复，并支持 AI 生成回复草稿。comment_key 用平台评论 ID，
    平台不返回 ID 时用「作者+内容+时间」指纹，保证重复采集不会入库两遍。
    """
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    account_id: int = Field(index=True)

    platform: str = ""
    work_id: str = Field(default="", index=True)     # 对接 WorkStat.work_id
    work_title: str = ""
    work_url: str = ""

    comment_key: str = Field(default="", index=True)  # 平台 ID 或内容指纹
    parent_key: str = ""                              # 回复所属的父评论
    author: str = ""
    content: str = ""
    created_text: str = ""                            # 平台展示的时间文案（如「2天前」）
    is_author: bool = False                           # 是否是账号本人发的评论/回复

    status: str = Field(default="new", index=True)    # new/drafted/replied/ignored
    ai_draft: str = ""
    replied_at: datetime | None = None
    collected_at: datetime = Field(default_factory=now, index=True)


class PrivateMessage(SQLModel, table=True):
    """平台私信（会话侧最近一条 + 未读数）。

    why 单独建表：私信和评论的结构差异大（按会话不按作品），且只需要
    「谁、说了什么、几条未读」的摘要用于飞书提醒，不做回复功能。
    msg_key：视频号用平台 msgId，抖音用「会话名+最后一条预览」指纹。
    """
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    account_id: int = Field(index=True)

    platform: str = ""
    sender: str = ""                                # 会话名/发送人昵称
    content: str = ""                               # 消息内容/预览
    msg_key: str = Field(default="", index=True)    # 平台 ID 或内容指纹
    session_id: str = ""                            # 会话 ID（视频号发送回复要用）
    peer_username: str = ""                         # 对方 username（发送兜底）
    unread_count: int = 0
    is_group: bool = False
    created_text: str = ""                      # 平台时间文案（昨天 16:49 / 10/29）
    ai_draft: str = ""
    history: str = ""                           # 最近多轮对话 JSON：[{"from_peer":bool,"content":str,"ts":int}]
    replied_at: datetime | None = None

    collected_at: datetime = Field(default_factory=now, index=True)


class StatsRun(SQLModel, table=True):
    """采集打卡：记录某天已跑过数据采集，避免调度器每 30 秒重复开浏览器。"""
    id: int | None = Field(default=None, primary_key=True)
    day: str = Field(default="", index=True)
    ran_at: datetime = Field(default_factory=now)
    note: str = ""
