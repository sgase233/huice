"""配置加载：缺变量只报变量名，模式必填项与安全基线校验。"""

from __future__ import annotations

import pytest

from app.config import ConfigError, Settings

BASE_ENV = {
    "APP_ENV": "local",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
    "DATABASE_URL": "postgresql://huice:pw@127.0.0.1:5432/huice",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "CORS_ORIGINS": "http://127.0.0.1:5173",
    "MODEL_MODE": "fixture",
    "OBJECT_STORE_MODE": "local",
    "LOCAL_OBJECT_DIR": ".local-objects",
    "ADMIN_TOKEN": "0123456789abcdef0123456789abcdef",
    "DEMO_ACCESS_MODE": "local",
}


def env(**overrides) -> dict[str, str]:
    values = dict(BASE_ENV)
    for key, value in overrides.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    return values


def error_names(exc: ConfigError) -> str:
    return str(exc)


def test_minimal_local_env_is_accepted():
    settings = Settings.from_env(env())
    assert settings.app_env == "local"
    assert settings.demo_access_mode == "local"
    assert settings.admin_token == BASE_ENV["ADMIN_TOKEN"]


def test_missing_admin_token_reports_name_only():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(ADMIN_TOKEN=None))
    message = error_names(info.value)
    assert "ADMIN_TOKEN" in message
    assert BASE_ENV["ADMIN_TOKEN"] not in message


def test_short_admin_token_is_rejected():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(ADMIN_TOKEN="short"))
    assert "ADMIN_TOKEN" in error_names(info.value)


def test_admin_token_value_is_never_echoed_on_other_errors():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(CORS_ORIGINS="*"))
    message = error_names(info.value)
    assert "CORS_ORIGINS" in message
    assert BASE_ENV["ADMIN_TOKEN"] not in message


@pytest.mark.parametrize(
    "origin",
    ["*", "http://127.0.0.1:5173/path", "ftp://example.com", "example.com"],
)
def test_invalid_cors_origins_are_rejected(origin):
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(CORS_ORIGINS=origin))
    assert "CORS_ORIGINS" in error_names(info.value)


def test_non_loopback_host_is_rejected_in_local_mode():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(API_HOST="0.0.0.0"))
    assert "API_HOST" in error_names(info.value)


def test_deepseek_mode_requires_model_settings():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(MODEL_MODE="deepseek"))
    message = error_names(info.value)
    for name in ("DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_API_KEY"):
        assert name in message


def test_deepseek_mode_reports_key_name_without_value():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(
            env(
                MODEL_MODE="deepseek",
                DEEPSEEK_BASE_URL="https://api.deepseek.com",
                DEEPSEEK_MODEL="deepseek-chat",
            )
        )
    message = error_names(info.value)
    assert "DEEPSEEK_API_KEY" in message
    assert "deepseek-chat" not in message
    assert "api.deepseek.com" not in message


def test_deepseek_mode_rejects_non_http_base_url():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(
            env(
                MODEL_MODE="deepseek",
                DEEPSEEK_BASE_URL="api.deepseek.com",
                DEEPSEEK_MODEL="deepseek-chat",
                DEEPSEEK_API_KEY="k-0123456789",
            )
        )
    assert "DEEPSEEK_BASE_URL" in error_names(info.value)


def test_maas_mode_is_no_longer_accepted():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(MODEL_MODE="maas"))
    assert "MODEL_MODE" in error_names(info.value)


def test_deepseek_mode_accepts_complete_settings():
    settings = Settings.from_env(
        env(
            MODEL_MODE="deepseek",
            DEEPSEEK_BASE_URL="https://api.deepseek.com",
            DEEPSEEK_MODEL="deepseek-chat",
            DEEPSEEK_API_KEY="k-0123456789",
        )
    )
    assert settings.model_mode == "deepseek"
    assert settings.deepseek_model == "deepseek-chat"


def test_obs_mode_requires_bucket_endpoint_and_credentials():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(OBJECT_STORE_MODE="obs"))
    message = error_names(info.value)
    for name in ("OBS_BUCKET", "OBS_ENDPOINT", "OBS_AK", "OBS_SK"):
        assert name in message


def test_obs_mode_reports_credential_names_without_values():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(
            env(OBJECT_STORE_MODE="obs", OBS_BUCKET="huice-policies", OBS_AK="AK-only")
        )
    message = error_names(info.value)
    assert "OBS_SK" in message
    assert "AK-only" not in message
    assert "huice-policies" not in message


def test_obs_mode_accepts_complete_settings():
    settings = Settings.from_env(
        env(
            OBJECT_STORE_MODE="obs",
            OBS_BUCKET="huice-policies",
            OBS_ENDPOINT="obs.cn-south-1.myhuaweicloud.com",
            OBS_AK="AK-test",
            OBS_SK="SK-test",
        )
    )
    assert settings.object_store_mode == "obs"
    assert settings.obs_endpoint == "obs.cn-south-1.myhuaweicloud.com"


def test_demo_env_requires_restricted_mode():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(APP_ENV="demo"))
    assert "DEMO_ACCESS_MODE" in error_names(info.value)


def test_local_env_rejects_restricted_mode():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(DEMO_ACCESS_MODE="restricted"))
    assert "DEMO_ACCESS_MODE" in error_names(info.value)


def test_database_url_must_be_postgres():
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env(DATABASE_URL="sqlite:///local.db"))
    assert "DATABASE_URL" in error_names(info.value)


def test_local_object_path_is_absolute():
    settings = Settings.from_env(env())
    assert settings.local_object_path.is_absolute()
