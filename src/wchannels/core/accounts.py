"""账号管理：CRUD、分组、登录态健康度、档案路径。"""
from sqlmodel import Session, select
from datetime import datetime, timezone
from pathlib import Path
from ..models import Account, AccountStatus
from ..config import settings
from ..timeutils import now


def list_accounts(session: Session, user_id: int) -> list[Account]:
    return list(session.exec(select(Account).where(Account.user_id == user_id).order_by(Account.id)).all())


def get_account(session: Session, account_id: int, user_id: int | None = None) -> Account | None:
    """按 ID 取账号。传入 user_id 则做归属校验（越权访问按不存在处理）。

    面向用户的 API 必须传 user_id，否则任意登录用户可改删他人账号、
    或对他人账号发起扫码登录（会拿到别人的登录态）。
    """
    a = session.get(Account, account_id)
    if a is None:
        return None
    if user_id is not None and a.user_id != user_id:
        return None
    return a


def create_account(session: Session, user_id: int, name: str, group: str = "", platform: str = "wechat") -> Account:
    acc = Account(
        user_id=user_id,
        name=name,
        group=group,
        platform=platform,
        profile_dir=f"acc_{platform}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        status=AccountStatus.ACTIVE.value,
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    # 建档案目录（按平台隔离，避免跨平台串号）
    (settings.profiles_dir / acc.profile_dir).mkdir(parents=True, exist_ok=True)
    return acc


def delete_account(session: Session, account: Account) -> None:
    session.delete(account)
    session.commit()


def mark_paused(session: Session, account: Account, paused: bool = True) -> None:
    account.status = AccountStatus.PAUSED.value if paused else AccountStatus.ACTIVE.value
    session.add(account)
    session.commit()


def detect_login(session: Session, account: Account, nickname: str,
                 logged_in: bool | None = None) -> None:
    """登录态探针回写。

    logged_in 显式给出时以它为准；不给则沿用旧规则（昵称非空=已登录）。
    各平台 DOM 差异大，抓不到昵称但页面确实不在登录页时仍算登录成功。
    """
    if logged_in is None:
        logged_in = bool(nickname)
    if nickname:
        account.nickname_detected = nickname
    account.last_login_at = now()
    account.status = AccountStatus.ACTIVE.value if logged_in else AccountStatus.LOGGED_OUT.value
    session.add(account)
    session.commit()


def _reset_daily_count_if_new_day(session: Session, account: Account) -> None:
    """跨天时把今日已发数归零。

    此前 daily_publish_count 只加不减，账号发满上限后即便换到第二天
    仍会被判定「达上限」，工作台用一天就瘫。
    """
    if not account.daily_publish_count:
        return
    last = account.last_publish_at
    if last is None:
        today_first = True
    else:
        if last.tzinfo is None:  # 兼容历史 naive 数据
            last = last.replace(tzinfo=timezone.utc)
        today_first = last.astimezone().date() != now().astimezone().date()
    if today_first:
        account.daily_publish_count = 0
        session.add(account)
        session.commit()


def refresh_daily_count(session: Session, account: Account) -> None:
    """对外暴露的跨天归零（账号列表/配额展示前调用，让界面数字实时准确）。"""
    _reset_daily_count_if_new_day(session, account)


def daily_limit_of(account: Account) -> int:
    """该账号所属平台的每日发布上限。"""
    return settings.PLATFORM_DAILY_LIMITS.get(account.platform, settings.PUBLISH_DAILY_LIMIT)


def can_publish(session: Session, account: Account) -> tuple[bool, str]:
    """检查该号现在能不能发。返回 (是否允许, 原因)。"""
    _reset_daily_count_if_new_day(session, account)
    if account.status == AccountStatus.PAUSED.value:
        return False, "账号正在被手动操作（扫码/打开发表页），请稍后再试"
    if account.status == AccountStatus.LOGGED_OUT.value:
        return False, "登录态已失效，请到「账号管理」点该账号的「登录（扫码）」重新扫码"
    daily_limit = daily_limit_of(account)
    if account.daily_publish_count >= daily_limit:
        return False, f"今日已发 {account.daily_publish_count} 条，达 {account.platform} 上限 {daily_limit}"
    return True, ""
