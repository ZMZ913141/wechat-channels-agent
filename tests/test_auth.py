"""鉴权：会话令牌签发/校验/过期。"""
import base64
import hashlib
import hmac
import json
import time

from wchannels.config import settings
from wchannels.web.auth import create_session_token, parse_session


def test_session_roundtrip():
    tok = create_session_token(7)
    assert parse_session(tok) == 7


def test_session_expired():
    payload = json.dumps({"uid": 7, "exp": int(time.time()) - 10})
    b = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(settings.SECRET_KEY.encode(), b.encode(), hashlib.sha256).hexdigest()
    tok = f"{b}.{sig}"
    assert parse_session(tok) is None
