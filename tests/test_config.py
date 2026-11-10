import pytest
from airlock.config import Settings


def test_model_overrides_read_process_environment(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "test-agent-model")
    monkeypatch.setenv("JUDGE_MODEL", "test-judge-model")
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    settings = Settings.from_env()
    assert settings.agent_model == "test-agent-model"
    assert settings.judge_model == "test-judge-model"
    assert settings.resolve_agent_mode() == "live"


def test_live_default_and_provider_specific_key_fallback():
    assert Settings(openai_api_key="test-key").resolve_agent_mode() == "live"
    assert Settings(anthropic_api_key="test-key").resolve_agent_mode() == "compromised"
    assert Settings(agent_provider="anthropic", anthropic_api_key="test-key").resolve_agent_mode() == "live"
    assert Settings(agent_provider="anthropic", openai_api_key="test-key").resolve_agent_mode() == "compromised"
    assert Settings().agent_model == "gpt-4o-mini"
    assert Settings(agent_provider="anthropic").agent_model == "claude-sonnet-5-5"
    assert Settings(agent_mode="live").resolve_agent_mode() == "compromised"
    assert Settings().resolve_agent_mode("live") == "compromised"


def test_legacy_demo_default_can_be_overridden_by_explicit_live_selection():
    settings = Settings(openai_api_key="test-key", agent_mode="live", demo_mode=True)
    assert settings.resolve_agent_mode() == "compromised"
    assert settings.resolve_agent_mode("live") == "live"


def test_invalid_agent_mode_rejected():
    with pytest.raises(ValueError, match="AGENT_MODE"):
        Settings(agent_mode="unknown")


def test_invalid_agent_provider_rejected():
    with pytest.raises(ValueError, match="AGENT_PROVIDER"):
        Settings(agent_provider="unknown")


def test_channel3_wallet_and_mode_environment(monkeypatch):
    monkeypatch.setenv("CHANNEL3_API_KEY", "test-catalog-key")
    monkeypatch.setenv("SANDBOX_STARTING_BALANCE", "4000.50")
    monkeypatch.setenv("AIRLOCK_MODE", "Enforce")
    settings = Settings.from_env()
    assert settings.channel3_api_key == "test-catalog-key"
    assert str(settings.sandbox_starting_balance) == "4000.50"
    assert settings.initial_mode == "Enforce"
    for kwargs in [{"initial_mode":"Off"},{"sandbox_starting_balance":"NaN"},{"sandbox_starting_balance":"-1"}]:
        with pytest.raises(ValueError):
            Settings(**kwargs)
