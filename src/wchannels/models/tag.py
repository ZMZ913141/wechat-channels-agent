"""标签：用户自定义标签，支持素材多对多。"""
from sqlmodel import SQLModel, Field


class Tag(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    name: str = Field(index=True)
    color: str = "#9aa0aa"     # 标签颜色
    category: str = ""          # 可选：brand / topic / status


class MaterialTag(SQLModel, table=True):
    material_id: int = Field(primary_key=True, foreign_key="material.id")
    tag_id: int = Field(primary_key=True, foreign_key="tag.id")
