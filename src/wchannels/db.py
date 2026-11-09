"""SQLite + SQLModel 引擎与会话工厂。

引擎惰性创建：首次 get_engine() 时才按当前 settings.WX_DATA_DIR 绑定数据库路径，
避免“导入即绑定”导致的测试/多实例路径污染。
"""
from sqlmodel import SQLModel, create_engine, Session
from .config import settings

_engine = None


def get_engine():
    """惰性单例引擎：第一次调用时按当前 WX_DATA_DIR 创建。"""
    global _engine
    if _engine is None:
        _engine = create_engine(
            f"sqlite:///{settings.db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    # 触发模型注册
    from . import models  # noqa: F401
    SQLModel.metadata.create_all(get_engine())
    # 兼容旧库：Account 新增 platform 列时，给已存在的表补列（SQLite 不破坏数据）
    _ensure_columns()


def _ensure_columns() -> None:
    """兼容旧库：为已存在的表补齐新增列（SQLite ADD COLUMN 不破坏既有数据）。"""
    from sqlmodel import text

    # (表名, 列名, 列定义)
    migrations = [
        ("account", "platform", "VARCHAR DEFAULT 'wechat'"),
        ("material", "collection", "VARCHAR DEFAULT ''"),
        ("material", "tags", "VARCHAR DEFAULT ''"),
        ("material", "ai_draft", "VARCHAR DEFAULT ''"),
        ("publishtarget", "collection", "VARCHAR DEFAULT ''"),
        ("publishtarget", "cover_path", "VARCHAR DEFAULT ''"),
        ("publishtarget", "comment", "VARCHAR DEFAULT ''"),
        ("publishtarget", "original_work", "BOOLEAN DEFAULT 0"),
        ("publishtarget", "visibility", "VARCHAR DEFAULT 'public'"),
        ("materialaccountconfig", "visibility", "VARCHAR DEFAULT 'public'"),
        ("privatemessage", "ai_draft", "VARCHAR DEFAULT ''"),
        ("privatemessage", "session_id", "VARCHAR DEFAULT ''"),
        ("privatemessage", "peer_username", "VARCHAR DEFAULT ''"),
        ("privatemessage", "replied_at", "DATETIME"),
        ("privatemessage", "history", "VARCHAR DEFAULT ''"),
    ]
    try:
        with get_engine().connect() as conn:
            for table, col, ddl in migrations:
                cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()]
                if cols and col not in cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                    conn.commit()
            # 原创互斥的数据库兜底：同一素材最多只能有一个账号 original_work=1，
            # 防止两个请求同时通过「先查后写」的校验（本地单机概率低，但加上更稳）。
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_mat_original "
                    "ON materialaccountconfig(material_id) WHERE original_work = 1"
                ))
                conn.commit()
            except Exception:
                # 旧数据里已有多条原创时不阻断启动（仍由接口层 400 兜住）
                pass
            # 旧版本的占位默认值「AI 制作」会对真人实拍误打 AI 标；
            # 它不是你填的文案，统一清空，让「留空=不标注」真正成立。
            try:
                conn.execute(text(
                    "UPDATE material SET ai_disclosure='' WHERE ai_disclosure='AI 制作'"
                ))
                conn.execute(text(
                    "UPDATE publishtarget SET ai_disclosure='' WHERE ai_disclosure='AI 制作'"
                ))
                conn.commit()
            except Exception:
                pass
    except Exception:
        # 表尚未创建等情况，忽略
        pass


def get_session() -> Session:
    """FastAPI 依赖注入用：生成一个请求级会话。"""
    with Session(get_engine()) as session:
        yield session
