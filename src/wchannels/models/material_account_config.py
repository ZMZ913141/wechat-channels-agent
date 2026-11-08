"""素材×账号 的独立发布配置。

同一条素材发到不同账号时，标题/描述/话题/合集可能完全不同；
本表存「这条素材发到这个号时用什么配置」，发布时优先取这里，
没配则回退到素材默认值。
"""
from sqlmodel import SQLModel, Field
from datetime import datetime
from ..timeutils import now


class MaterialAccountConfig(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    material_id: int = Field(index=True, foreign_key="material.id")
    account_id: int = Field(index=True, foreign_key="account.id")

    short_title: str = ""
    description: str = ""
    topics: str = ""
    collection: str = ""         # 该账号平台的合集名
    ai_disclosure: str = ""      # 留空 = 不强制标注，用素材默认
    cover_path: str = ""
    original_work: bool = False  # 是否勾选「原创」（部分平台二选一，按平台互斥）
    comment: str = ""            # 该号首条评论
    visibility: str = "public"   # 可见范围：public 公开 / private 仅自己可见

    updated_at: datetime = Field(default_factory=now)
