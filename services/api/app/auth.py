"""账号与会话的密码学原语（E1，#46）。

这里只放**纯函数**：口令哈希、令牌生成与摘要、用户名规范化。判定规则（真值表、
角色、归属、CSRF）在 :mod:`app.identity`，两处分开是为了让这一层能被直接单测，
也不需要数据库或请求对象。

三条口径：

1. **口令用 stdlib 的 scrypt**，不引入新依赖（``requirements.lock`` 里没有
   passlib/bcrypt/argon2）。存储格式自带参数：``scrypt$n$r$p$salt$hash``，
   以后调参不需要迁移脚本——旧行按自己的参数验证。
2. **会话令牌与 CSRF 值都只以摘要入账**：Cookie 明文只在 ``Set-Cookie`` 出现一次。
3. **CSRF 值由会话令牌派生**，不入库明文：``/auth/me`` 必须能返回与会话建立时
   **完全相同**的值（契约 AUTH-04），而库里只有摘要。派生既满足这条，又不需要
   把明文写进库。跨站攻击者读不到 HttpOnly 的会话 Cookie，也就算不出这个值。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

#: 与契约 ``RegisterRequest.password`` 的 minLength 一致（#55 draft.4）。
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 32

USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,32}$")

SESSION_COOKIE = "huice_session"
#: 与 ``infra/sql/009_users_sessions.sql`` 的 CHECK 一致。
SESSION_PATH = "/api"
CSRF_HEADER = "X-CSRF-Token"

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_BYTES = 16
_SCRYPT_MAXMEM = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PasswordHash:
    """一条 ``scrypt$n$r$p$salt$hash`` 的可解析形态；参数随记录走。"""

    n: int
    r: int
    p: int
    salt: bytes
    digest: bytes


def hash_password(password: str) -> str:
    """生成可入库的口令哈希；每次都用新的随机盐。"""

    salt = secrets.token_bytes(_SCRYPT_SALT_BYTES)
    digest = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def parse_password_hash(stored: str) -> PasswordHash | None:
    """解析不认识的格式一律返回 ``None``（当作验证失败），不抛异常。"""

    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = bytes.fromhex(parts[4])
        digest = bytes.fromhex(parts[5])
    except ValueError:
        return None
    if n <= 1 or r <= 0 or p <= 0 or not salt or not digest:
        return None
    return PasswordHash(n=n, r=r, p=p, salt=salt, digest=digest)


def verify_password(password: str, stored: str) -> bool:
    """常量时间比较；参数越界或格式损坏都返回 False，不向调用方区分原因。"""

    parsed = parse_password_hash(stored)
    if parsed is None:
        return False
    try:
        candidate = _scrypt(password, parsed.salt, parsed.n, parsed.r, parsed.p)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(candidate, parsed.digest)


def password_is_acceptable(password: str) -> bool:
    return MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )


def new_session_token() -> str:
    """会话 Cookie 的明文值；43 个 URL 安全字符，熵 256 位。"""

    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    """入库摘要：sha256 十六进制。库内与日志里都不出现明文。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_token_for(session_token: str) -> str:
    """由会话令牌派生 CSRF 值：32 位十六进制，登录与 ``/auth/me`` 返回同一个值。"""

    return hashlib.sha256(f"huice-csrf:{session_token}".encode("utf-8")).hexdigest()[:32]


def new_demo_sid() -> str:
    """演示会话的 ``sid``：16 字节随机，随 demo cookie 一起签名。"""

    return secrets.token_hex(16)


def normalize_username(raw: str) -> str:
    """注册与登录共用同一规范化：去首尾空白 + 小写。"""

    return raw.strip().lower()


def username_is_valid(username: str) -> bool:
    return bool(USERNAME_PATTERN.match(username))
