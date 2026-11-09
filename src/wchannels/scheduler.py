"""后台调度器：定时轮询发布队列，自动执行到点的任务。"""
import random
import threading
import time
from datetime import datetime
from sqlmodel import Session, select
from .config import settings
from .db import get_engine
from .core import queue as queue_svc
from .core import accounts as acc_svc
from .core import audit as audit_svc
from .logutil import get_logger
from .models import Account, Material, AuditSource, StatsRun
from .browser.launcher import launch_context
from .browser.platforms import publish_for_account
from .browser.proclock import AccountLock, ProfileLock, LockBusy

log = get_logger("scheduler")
PLATFORM_CN = {"wechat": "视频号", "douyin": "抖音", "xiaohongshu": "小红书"}


class Scheduler:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._running = False
        self._next_keepalive: float | None = None

    def start(self):
        if not settings.SCHEDULER_ENABLED:
            log.info("调度器已禁用")
            return
        self._purge_audit()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("调度器已启动，每 %ss 检查队列", settings.SCHEDULER_INTERVAL_S)

    @staticmethod
    def _purge_audit():
        """启动时按保留期清理旧审计记录（AUDIT_RETENTION_DAYS，0=永久保留）。"""
        days = settings.AUDIT_RETENTION_DAYS
        if days <= 0:
            return
        try:
            with Session(get_engine()) as session:
                n = audit_svc.purge_older_than(session, days)
            if n:
                log.info("已清理 %d 条 %d 天前的审计记录", n, days)
        except Exception as e:
            log.warning("审计清理失败（不影响启动）: %s", e)

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception:
                log.exception("调度器轮询异常")
            try:
                self._stats_tick()
            except Exception:
                log.exception("数据采集检查异常")
            try:
                self._keepalive_tick()
            except Exception:
                log.exception("登录态保活检查异常")
            time.sleep(settings.SCHEDULER_INTERVAL_S)

    def _stats_tick(self):
        """每天 STATS_COLLECT_HOUR 点后跑一次作品数据采集（独立线程，不挡发布队列）。"""
        if not settings.STATS_COLLECT_ENABLED:
            return
        local = datetime.now()
        if local.hour < settings.STATS_COLLECT_HOUR:
            return
        day = local.strftime("%Y-%m-%d")
        with Session(get_engine()) as session:
            if session.exec(select(StatsRun).where(StatsRun.day == day)).first():
                return
            session.add(StatsRun(day=day, note="auto"))
            session.commit()
        threading.Thread(target=self._stats_job, args=(day,), daemon=True).start()

    def _stats_job(self, day: str):
        from .core import workstats
        try:
            with Session(get_engine()) as session:
                results = workstats.collect_all(session)
            ok = [r for r in results if r["works"]]
            log.info("数据采集完成（%s）：%d 个账号有作品，共写入 %d 条快照",
                     day, len(ok), sum(r["saved"] for r in results))
            for r in results:
                if r["error"] and not r["works"]:
                    log.warning("数据采集 %s 失败：%s", r["account_name"], r["error"])
            from .core import notify
            notify.send(
                "作品数据已采集",
                [f"{r['account_name']}（{PLATFORM_CN.get(r['platform'], r['platform'])}）："
                 f"{r['works']} 条作品" + ("" if r["works"] else f"｜{r['error']}")
                 for r in results],
                level="data",
            )
            if settings.COMMENTS_COLLECT_ENABLED:
                from .core import comments as comments_svc
                with Session(get_engine()) as session:
                    cres = comments_svc.collect_all(session)
                for r in cres:
                    if r["error"]:
                        log.warning("评论采集 %s：%s", r["account_name"], r["error"])
                # 私信采集跟着评论一起走（同样的一天一轮）
                from .core import messages as messages_svc
                with Session(get_engine()) as session:
                    mres = messages_svc.collect_all(session)
                for r in mres:
                    if r["error"]:
                        log.warning("私信采集 %s：%s", r["account_name"], r["error"])
                # 登录态巡检：逐账号快速探测，掉线的写回状态并飞书提醒重扫
                offline = self._probe_logins(only_new=True)
                if offline:
                    from .core import notify
                    notify.send(
                        f"登录态巡检：{len(offline)} 个账号掉线",
                        [f"{o['name']}（{PLATFORM_CN.get(o['platform'], o['platform'])}）："
                         f"登录态失效，请到「账号管理」重新扫码" for o in offline],
                        level="fail",
                    )
        except Exception:
            log.exception("数据采集失败")

    def _keepalive_tick(self):
        """到点启动一轮登录态保活（后台线程跑，不阻塞发布队列）。

        原理：打开一次账号创作者后台是真实访问；若平台对 session 做滑动续期，
        这一下就能延长登录态。同时兼作巡检，掉线立即告警。
        频率与时间抖动都可配，默认 3 小时一轮、±10% 抖动。
        """
        if not settings.LOGIN_KEEPALIVE_ENABLED:
            return
        now = time.time()
        if self._next_keepalive is None:
            delay = max(60, settings.LOGIN_KEEPALIVE_START_DELAY_S)
            self._next_keepalive = now + delay
            log.info("登录态保活已启用：%d 分钟后跑首轮，之后每 %d 小时一轮",
                     delay // 60, settings.LOGIN_KEEPALIVE_INTERVAL_S // 3600)
            return
        if now < self._next_keepalive:
            return
        # 先预约下一轮再干活：本轮异常也不会打乱节奏，更不会紧循环重试
        interval = max(1800, settings.LOGIN_KEEPALIVE_INTERVAL_S)
        self._next_keepalive = now + interval * random.uniform(0.9, 1.1)
        threading.Thread(target=self._keepalive_job, daemon=True).start()

    def _keepalive_job(self):
        from .core import notify
        try:
            offline = self._probe_logins(
                only_new=True, stagger_s=max(0, settings.LOGIN_KEEPALIVE_STAGGER_S))
            log.info("登录态保活巡检完成：%d 个账号新掉线", len(offline))
            if offline:
                notify.send(
                    f"账号掉线提醒：{len(offline)} 个需要重新扫码",
                    [f"{o['name']}（{PLATFORM_CN.get(o['platform'], o['platform'])}）："
                     f"登录态已失效，请到「账号管理」点「登录（扫码）」" for o in offline],
                    level="fail",
                )
        except Exception:
            log.exception("登录态保活巡检异常")

    def _probe_logins(self, only_new: bool = False, stagger_s: int = 0) -> list:
        """登录态巡检：逐账号快速探测并回写状态，返回掉线账号列表。

        与采集/发布同一把账号锁+profile锁：撞上正忙的账号本轮跳过（不判掉线），
        避免把「正在发布」误判成「掉线」。

        only_new=True 时只返回「本次新掉线」的账号（库中原本就是 logged_out 的
        不再重复提醒），避免每轮巡检都刷同一条飞书通知。
        stagger_s > 0 时账号之间随机错峰等待，模拟自然访问节奏。
        """
        from .browser.loginprobe import probe_login
        offline = []
        with Session(get_engine()) as session:
            accounts = list(session.exec(select(Account)).all())
        for idx, a in enumerate(accounts):
            if stagger_s and idx:
                time.sleep(random.uniform(0, stagger_s))
            prev_status = a.status
            profile = settings.profiles_dir / a.profile_dir
            a_lock, p_lock = AccountLock(a.id), ProfileLock(profile)
            try:
                a_lock.acquire()
                p_lock.acquire()
            except LockBusy:
                log.info("巡检跳过（账号正忙）：%s", a.name)
                continue
            try:
                logged_in, nick = probe_login(profile, a.platform)
                with Session(get_engine()) as session:
                    row = session.get(Account, a.id)
                    if row:
                        acc_svc.detect_login(session, row, nick, logged_in)
                if not logged_in:
                    if only_new and prev_status == "logged_out":
                        log.info("巡检：%s 仍处掉线状态（不重复提醒）", a.name)
                    else:
                        log.warning("巡检发现掉线：%s（%s）", a.name, a.platform)
                        offline.append({"name": a.name, "platform": a.platform})
            finally:
                a_lock.release()
                p_lock.release()
        return offline

    def _tick(self):
        with Session(get_engine()) as session:
            due = queue_svc.due_targets(session)
            if not due:
                return
            for t in due:
                if not self._running:
                    break
                m = session.get(Material, t.material_id)
                a = session.get(Account, t.account_id)
                if not m:
                    # 素材已被彻底删除：标记失败，否则任务会每 30 秒被反复取出
                    queue_svc.mark_failed(session, t, "跳过：素材已被删除")
                    continue
                if not a:
                    queue_svc.mark_failed(session, t, "跳过：账号已被删除")
                    continue
                # 素材被移入回收站/彻底删除后不能继续发，否则会把删掉的视频发出去
                if m.status == "trash":
                    queue_svc.mark_failed(session, t, "跳过：素材已删除（在回收站）")
                    audit_svc.record(
                        session, user_id=a.user_id, account_id=a.id, material_id=m.id,
                        platform=a.platform, ok=False, message="跳过：素材已删除（在回收站）",
                        source=AuditSource.SKIPPED.value,
                        target_id=t.id, batch_id=t.batch_id,
                    )
                    continue
                ok, reason = acc_svc.can_publish(session, a)
                if not ok:
                    queue_svc.mark_failed(session, t, f"跳过：{reason}")
                    audit_svc.record(
                        session, user_id=a.user_id, account_id=a.id, material_id=m.id,
                        platform=a.platform, ok=False, message=f"跳过：{reason}",
                        source=AuditSource.SKIPPED.value,
                        target_id=t.id, batch_id=t.batch_id,
                    )
                    continue
                self._execute_one(session, t, a, m)

    def _execute_one(self, session, target, account, material):
        import time
        a_lock = AccountLock(account.id)
        profile = settings.profiles_dir / account.profile_dir
        p_lock = ProfileLock(profile)
        started = time.monotonic()
        try:
            a_lock.acquire()
            p_lock.acquire()
            queue_svc.mark_running(session, target)
            with launch_context(profile, headless=settings.PUBLISH_HEADLESS) as ctx:
                ok, msg, shot = publish_for_account(
                    ctx, account,
                    video_path=material.file_path,
                    short_title=target.short_title or material.short_title,
                    description=target.description or material.description,
                    topics=target.topics or material.topics,
                    ai_disclosure=target.ai_disclosure or material.ai_disclosure,
                    collection=target.collection or material.collection,
                    cover_path=target.cover_path or material.cover_path,
                    original_work=bool(target.original_work),
                    visibility=target.visibility or "public",
                )
            cost = int((time.monotonic() - started) * 1000)
            if ok:
                queue_svc.mark_success(session, target, account, material)
                log.info("发布成功：%s → %s", material.title, account.name)
            else:
                queue_svc.mark_failed(session, target, msg, shot)
                log.warning("发布失败：%s → %s：%s", material.title, account.name, msg)
            audit_svc.record(
                session, user_id=account.user_id, account_id=account.id,
                material_id=material.id, platform=account.platform,
                ok=ok, message=msg, source=AuditSource.SCHEDULER.value,
                target_id=target.id, batch_id=target.batch_id,
                screenshot=shot, duration_ms=cost,
            )
            from .core import notify
            notify.send(
                "发布成功" if ok else "发布失败",
                [f"账号：{account.name}（{PLATFORM_CN.get(account.platform, account.platform)}）",
                 f"素材：{material.title}"] + ([] if ok else [f"原因：{msg}"]),
                level="success" if ok else "fail",
            )
        except LockBusy:
            # 锁被面板手动发布等活进程持有：保持 queued，下一轮 30s 自然重试。
            # 绝不能标失败——一次撞车就把任务判死，是小白最难懂的坑。
            log.info("账号 %s 正忙，稍后重试：%s", account.name, material.title)
        except Exception as e:
            queue_svc.mark_failed(session, target, str(e))
            log.exception("发布异常：%s", e)
            audit_svc.record(
                session, user_id=account.user_id, account_id=account.id,
                material_id=material.id, platform=account.platform,
                ok=False, message=f"异常：{e}", source=AuditSource.SCHEDULER.value,
                target_id=target.id, batch_id=target.batch_id,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            a_lock.release()
            p_lock.release()


scheduler = Scheduler()
