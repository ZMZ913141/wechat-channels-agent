"""用户：本地多用户支持（简单用户名+密码 hash，单机用）。"""
from sqlmodel import SQLModel, Field
from datetime import datetime
import hashlib
import os
from ..timeutils import now


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    display_name: str = ""
    created_at: datetime = Field(default_factory=now)

    @staticmethod
    def hash_password(password: str) -> str:
        salt = os.urandom(16).hex()
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return f"{salt}${h.hex()}"

    def verify_password(self, password: str) -> bool:
        try:
            salt, expected = self.password_hash.split("$", 1)
            h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
            return h.hex() == expected
        except Exception:
            return False
