"""视频号账号：每号一个独立浏览器档案，隔离登录态。"""
from sqlmodel import SQLModel, Field
from datetime import datetime
from enum import Enum
from ..timeutils import now


class AccountStatus(str, Enum):
    ACTIVE = "active"
    LOGGED_OUT = "logged_out"
    PAUSED = "paused"  # 手动操作中（扫码/打开发表页）


class Account(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    name: str                      # 显示名，如 "珠珠 · 雅太社"
    group: str = ""                # 分组，如 "雅太社组"
    platform: str = "wechat"       # 所属平台：wechat / douyin / xiaohongshu
    profile_dir: str = ""          # 浏览器档案目录（相对 profiles_dir，按平台隔离）
    nickname_detected: str = ""    # 登录态实测到的视频号昵称
    status: str = AccountStatus.ACTIVE.value
    last_login_at: datetime | None = None
    last_publish_at: datetime | None = None
    daily_publish_count: int = 0   # 今日已发数
    default_topics: str = ""       # 该号默认话题，逗号分隔
    default_comment: str = ""      # 该号默认首条评论
    created_at: datetime = Field(default_factory=now)
