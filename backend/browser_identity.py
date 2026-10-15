"""服务端签发的匿名身份；客户端UID不具有授权能力，不替代未来账户登录。"""
import hashlib
import hmac
import os
import re
import secrets
import tempfile
from functools import lru_cache
from pathlib import Path

COOKIE = "poker_session"


@lru_cache(maxsize=4)
def _file_key(filename):
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # 完整写入后独占发布，避免多worker读到半个密钥。
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".session-key-", delete=False) as f:
            tmp = Path(f.name)
            f.write(secrets.token_bytes(32))
            f.flush()
            os.fsync(f.fileno())
        try:
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
        finally:
            tmp.unlink()
    key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("匿名会话密钥格式异常，停止签发")
    return key


def _key():
    configured = os.environ.get("POKER_SESSION_SECRET")
    if configured:
        if len(configured) < 32:
            raise RuntimeError("POKER_SESSION_SECRET至少需要32个字符")
        return configured.encode("utf-8")
    return _file_key(os.environ.get("POKER_SESSION_KEY_FILE") or str(
        Path(__file__).resolve().parent / "data" / "session.key"))


def identity(cookie):
    """返回(uid, 要签发的新cookie或None)。不接受UID认领旧档案。"""
    key = _key()
    if isinstance(cookie, str) and re.fullmatch(r"anon-[0-9a-f]{32}\.[0-9a-f]{64}", cookie):
        uid, signature = cookie.split(".")
        expected = hmac.new(key, uid.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            return uid, None
    uid = "anon-" + secrets.token_hex(16)
    signature = hmac.new(key, uid.encode(), hashlib.sha256).hexdigest()
    return uid, uid + "." + signature
