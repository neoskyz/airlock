import pytest
from airlock.config import Settings


def test_model_overrides_read_process_environment(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "test-agent-model")
    monkeypatch.setenv("JUDGE_MODEL", "test-judge-model")
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    settings = Settings.from_env()
    assert settings.agent_model == "test-agent-model"
    assert settings.judge_model == "test-judge-model"
    assert settings.resolve_agent_mode() == "live"


def test_compromised_default_and_no_key_live_fallback():
    assert Settings(anthropic_api_key="test-key").resolve_agent_mode() == "compromised"
    assert Settings(agent_mode="live").resolve_agent_mode() == "compromised"
    assert Settings().resolve_agent_mode("live") == "compromised"


def test_legacy_demo_default_can_be_overridden_by_explicit_live_selection():
    settings = Settings(anthropic_api_key="test-key", agent_mode="live", demo_mode=True)
    assert settings.resolve_agent_mode() == "compromised"
    assert settings.resolve_agent_mode("live") == "live"


def test_invalid_agent_mode_rejected():
    with pytest.raises(ValueError, match="AGENT_MODE"):
        Settings(agent_mode="unknown")
