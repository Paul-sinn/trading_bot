"""Step 0 smoke 테스트.

폴더 구조와 설정 스캐폴드가 제대로 import되는지 확인한다.
비즈니스 로직은 검증하지 않는다 (이후 step 범위).
"""

import importlib


def test_settings_importable_with_defaults():
    from backend.app.core.config import Settings

    settings = Settings()
    assert settings.database_url == "sqlite:///./trading_bot.db"
    assert settings.redis_url == "redis://localhost:6379/0"


def test_secrets_default_to_none():
    from backend.app.core.config import Settings

    settings = Settings()
    # Robinhood는 공개 API 키가 없다 — MCP 토글이 안전 기본값 False(실거래 없음).
    assert settings.robinhood_mcp_enabled is False
    assert settings.claude_api_key is None


def test_package_directories_importable():
    for module in ("backend.app", "agents", "algorithms"):
        assert importlib.import_module(module) is not None
