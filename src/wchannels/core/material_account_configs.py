"""素材×账号 独立配置的读写。"""
from sqlmodel import Session, select
from ..models import MaterialAccountConfig


def get_config(session: Session, material_id: int, account_id: int) -> MaterialAccountConfig | None:
    return session.exec(
        select(MaterialAccountConfig).where(
            MaterialAccountConfig.material_id == material_id,
            MaterialAccountConfig.account_id == account_id,
        )
    ).first()


def list_for_material(session: Session, material_id: int) -> list[MaterialAccountConfig]:
    return list(session.exec(
        select(MaterialAccountConfig).where(MaterialAccountConfig.material_id == material_id)
    ).all())


def has_other_original(session: Session, material_id: int, account_id: int) -> bool:
    """该素材下是否有【其他账号】已勾选原创（原创按素材全局互斥，平台间不能同时声明）。"""
    return session.exec(
        select(MaterialAccountConfig).where(
            MaterialAccountConfig.material_id == material_id,
            MaterialAccountConfig.account_id != account_id,
            MaterialAccountConfig.original_work == True,  # noqa: E712
        )
    ).first() is not None


def upsert(session: Session, material_id: int, account_id: int, **fields) -> MaterialAccountConfig:
    """新建或更新该素材×账号的配置。fields 为 short_title/description/topics/..."""
    cfg = get_config(session, material_id, account_id)
    if not cfg:
        cfg = MaterialAccountConfig(material_id=material_id, account_id=account_id)
    for k, v in fields.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return cfg
