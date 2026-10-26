"""Environment configuration loading and mode-based validation.

Load order: real environment variables win, then ``huice/.env`` when present.
Missing or invalid values fail startup by naming the variable only; values are
never echoed, logged, or included in error messages.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

APP_VERSION = "1.0.0-rc.2"

REPO_ROOT = Path(__file__).resolve().parents[3]

APP_ENVS = ("local", "demo")
MODEL_MODES = ("fixture", "deepseek")
OBJECT_STORE_MODES = ("local", "obs")
DEMO_ACCESS_MODES = ("local", "restricted")
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

MIN_ADMIN_TOKEN_LENGTH = 16
MIN_DEMO_SESSION_SECRET_LENGTH = 32


class ConfigError(RuntimeError):
    """Raised when the process cannot start because configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    database_url: str = ""
    redis_url: str = ""
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173",)
    model_mode: str = "fixture"
    object_store_mode: str = "local"
    local_object_dir: str = ".local-objects"
    admin_token: str = ""
    demo_access_mode: str = "local"
    demo_session_secret: str = ""
    deepseek_base_url: str = ""
    deepseek_model: str = ""
    deepseek_api_key: str = ""
    obs_bucket: str = ""
    obs_endpoint: str = ""
    obs_ak: str = ""
    obs_sk: str = ""

    @property
    def local_object_path(self) -> Path:
        path = Path(self.local_object_dir)
        return path if path.is_absolute() else REPO_ROOT / path

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        if environ is None:
            env_file = REPO_ROOT / ".env"
            if env_file.is_file():
                load_dotenv(env_file, override=False)
            env = os.environ

        problems: list[str] = []

        app_env = _text(env, "APP_ENV", "local")
        if app_env not in APP_ENVS:
            problems.append("APP_ENV")

        api_host = _text(env, "API_HOST", "127.0.0.1")
        if app_env == "local" and api_host not in LOOPBACK_HOSTS:
            problems.append("API_HOST")

        api_port = 8000
        raw_port = _text(env, "API_PORT", "8000")
        try:
            api_port = int(raw_port)
            if not 1 <= api_port <= 65535:
                problems.append("API_PORT")
        except ValueError:
            problems.append("API_PORT")

        database_url = _text(env, "DATABASE_URL", "")
        if not _is_postgres_url(database_url):
            problems.append("DATABASE_URL")

        redis_url = _text(env, "REDIS_URL", "")
        if redis_url and not redis_url.startswith(("redis://", "rediss://")):
            problems.append("REDIS_URL")

        cors_origins = _parse_origins(_text(env, "CORS_ORIGINS", "http://127.0.0.1:5173"))
        if not cors_origins:
            problems.append("CORS_ORIGINS")

        model_mode = _text(env, "MODEL_MODE", "fixture")
        if model_mode not in MODEL_MODES:
            problems.append("MODEL_MODE")

        object_store_mode = _text(env, "OBJECT_STORE_MODE", "local")
        if object_store_mode not in OBJECT_STORE_MODES:
            problems.append("OBJECT_STORE_MODE")

        local_object_dir = _text(env, "LOCAL_OBJECT_DIR", ".local-objects")
        if object_store_mode == "local" and not local_object_dir:
            problems.append("LOCAL_OBJECT_DIR")

        admin_token = _text(env, "ADMIN_TOKEN", "")
        if len(admin_token) < MIN_ADMIN_TOKEN_LENGTH:
            problems.append("ADMIN_TOKEN")

        demo_access_mode = _text(env, "DEMO_ACCESS_MODE", "local")
        if demo_access_mode not in DEMO_ACCESS_MODES:
            problems.append("DEMO_ACCESS_MODE")
        elif app_env == "demo" and demo_access_mode != "restricted":
            problems.append("DEMO_ACCESS_MODE")
        elif app_env == "local" and demo_access_mode != "local":
            problems.append("DEMO_ACCESS_MODE")

        # 演示会话密钥与后台 Bearer 令牌必须是两个独立机密：否则持有 ADMIN_TOKEN 的人
        # 可以自行签发会话，绕过网关门禁这一层（M2 二审 P1）。
        demo_session_secret = _text(env, "DEMO_SESSION_SECRET", "")
        if demo_access_mode == "restricted" and len(
            demo_session_secret
        ) < MIN_DEMO_SESSION_SECRET_LENGTH:
            problems.append("DEMO_SESSION_SECRET")

        deepseek_base_url = _text(env, "DEEPSEEK_BASE_URL", "")
        deepseek_model = _text(env, "DEEPSEEK_MODEL", "")
        deepseek_api_key = _text(env, "DEEPSEEK_API_KEY", "")
        if model_mode == "deepseek":
            for name, value in (
                ("DEEPSEEK_BASE_URL", deepseek_base_url),
                ("DEEPSEEK_MODEL", deepseek_model),
                ("DEEPSEEK_API_KEY", deepseek_api_key),
            ):
                if not value:
                    problems.append(name)
            if deepseek_base_url and not _is_https_or_http_url(deepseek_base_url):
                problems.append("DEEPSEEK_BASE_URL")

        obs_bucket = _text(env, "OBS_BUCKET", "")
        obs_endpoint = _text(env, "OBS_ENDPOINT", "")
        obs_ak = _text(env, "OBS_AK", "")
        obs_sk = _text(env, "OBS_SK", "")
        if object_store_mode == "obs":
            for name, value in (
                ("OBS_BUCKET", obs_bucket),
                ("OBS_ENDPOINT", obs_endpoint),
                ("OBS_AK", obs_ak),
                ("OBS_SK", obs_sk),
            ):
                if not value:
                    problems.append(name)

        if problems:
            unique = list(dict.fromkeys(problems))
            raise ConfigError(
                "启动失败，以下环境变量缺失或取值不合法（只列出变量名）：" + "、".join(unique)
            )

        return cls(
            app_env=app_env,
            api_host=api_host,
            api_port=api_port,
            database_url=database_url,
            redis_url=redis_url,
            cors_origins=cors_origins,
            model_mode=model_mode,
            object_store_mode=object_store_mode,
            local_object_dir=local_object_dir,
            admin_token=admin_token,
            demo_access_mode=demo_access_mode,
            demo_session_secret=demo_session_secret,
            deepseek_base_url=deepseek_base_url,
            deepseek_model=deepseek_model,
            deepseek_api_key=deepseek_api_key,
            obs_bucket=obs_bucket,
            obs_endpoint=obs_endpoint,
            obs_ak=obs_ak,
            obs_sk=obs_sk,
        )


def _text(env: dict[str, str], name: str, default: str) -> str:
    value = env.get(name)
    if value is None:
        return default
    return value.strip()


def _is_https_or_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def _is_postgres_url(value: str) -> bool:
    if not value.startswith(("postgresql://", "postgres://")):
        return False
    parsed = urlparse(value)
    return bool(parsed.hostname)


def _parse_origins(raw: str) -> tuple[str, ...]:
    origins: list[str] = []
    for part in raw.split(","):
        origin = part.strip()
        if not origin:
            continue
        if origin == "*":
            return ()
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ()
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            return ()
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)
