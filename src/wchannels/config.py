"""全局配置：所有路径与开关从 .env 读取，带默认值。"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 数据目录（收件箱固定是其中的 inbox 子目录，移动数据目录时一起走）
    WX_DATA_DIR: Path = Path(r"D:\wechat_channels_data")

    # 服务
    WX_HOST: str = "127.0.0.1"
    WX_PORT: int = 8734  # 不常用端口，避开 5000/8000/8080
    # 工作台对外地址：飞书卡片「打开工作台」按钮的链接。
    # 内网穿透开着时改成隧道公网 URL（pinggy/cloudflared），手机才能点开。
    WORKBENCH_URL: str = "http://127.0.0.1:8734"

    # 鉴权（本地部署：签名 Cookie 会话；生产请改 .env 覆盖）
    SECRET_KEY: str = "change-me-local-dev-wchannels"
    SESSION_EXPIRE_DAYS: int = 30

    # 调度器（自动执行到点的发布任务）
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_INTERVAL_S: int = 30  # 每 30 秒检查一次队列

    # 审计日志保留期（天）：调度器启动时清理更早的记录；0 = 永久保留
    AUDIT_RETENTION_DAYS: int = 90

    # 飞书群机器人 webhook（发布结果/数据日报通知；留空则不发）
    FEISHU_WEBHOOK_URL: str = ""

    # 发布后数据采集（每天打开各账号创作者后台读一次作品数据）
    STATS_COLLECT_ENABLED: bool = True
    STATS_COLLECT_HOUR: int = 9      # 每天 9 点后采集一次
    STATS_COLLECT_MIN_INTERVAL_H: int = 20  # 距上次采集不足这么多小时就不再采

    # 评论采集（跟作品数据一起跑；有新评论会发飞书通知）
    COMMENTS_COLLECT_ENABLED: bool = True

    # LLM
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    # 是否让 LLM 强制 JSON 输出（response_format=json_object）。
    # 本地聚合网关默认支持，开启后 AI 填写的 JSON 解析更稳；
    # 若换用不支持的网关，在 .env 里设 LLM_JSON_MODE=false 即可。
    LLM_JSON_MODE: bool = True

    # 浏览器：默认空 -> 用 Playwright 自带 Chromium（已随项目安装，无需另装 Chrome）
    # 若本机已装 Chrome 且想用，可设 BROWSER_CHANNEL=chrome
    BROWSER_CHANNEL: str = ""
    PUBLISH_HEADLESS: bool = True
    PUBLISH_UPLOAD_TIMEOUT_S: int = 3600
    PUBLISH_SAME_ACC_INTERVAL_MIN: int = 30
    PUBLISH_STAGGER_MIN: int = 30
    PUBLISH_DAILY_LIMIT: int = 3
    WAIT_BETWEEN_ACTIONS: float = 1.0

    # 视频号
    PUBLISH_URL: str = "https://channels.weixin.qq.com/platform/post/create"

    # 平台清单与每平台策略（发布 URL 由各适配器内置，这里放限额/错峰等通用策略）
    PLATFORMS: list[str] = ["wechat", "douyin", "xiaohongshu"]
    PLATFORM_DAILY_LIMITS: dict[str, int] = {"wechat": 3, "douyin": 5, "xiaohongshu": 5}
    PLATFORM_STAGGER_MIN: dict[str, int] = {"wechat": 30, "douyin": 20, "xiaohongshu": 20}

    @property
    def db_path(self) -> Path:
        return self.WX_DATA_DIR / "app.db"

    @property
    def profiles_dir(self) -> Path:
        return self.WX_DATA_DIR / "profiles"

    @property
    def logs_dir(self) -> Path:
        return self.WX_DATA_DIR / "logs"

    @property
    def shots_dir(self) -> Path:
        return self.WX_DATA_DIR / "shots"

    @property
    def thumbs_dir(self) -> Path:
        return self.WX_DATA_DIR / "thumbs"

    @property
    def inbox_dir(self) -> Path:
        return self.WX_DATA_DIR / "inbox"

    def ensure_dirs(self) -> None:
        for p in [self.WX_DATA_DIR, self.inbox_dir, self.profiles_dir,
                  self.logs_dir, self.shots_dir, self.thumbs_dir]:
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
