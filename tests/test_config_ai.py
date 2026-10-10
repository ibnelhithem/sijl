"""The [ai] settings: enabled, confirm, model and redact, through the same four layers as
every other setting. `SIJL_AI=off` and `--no-ai` turn AI off; `--yes` answers the one
confirmation for a command; the API key itself is never a setting."""

import pytest

from sijl.config import ConfigError, Settings
from tests.test_config import config_file, load


def test_ai_is_enabled_asks_first_and_redacts_by_default() -> None:
    settings = load().settings
    assert settings == Settings()
    assert (settings.ai_enabled, settings.ai_confirm, settings.ai_redact) == (True, True, True)
    assert settings.ai_model == "claude-opus-5-5"


def test_the_file_sets_the_ai_section() -> None:
    config_file('[ai]\nenabled = false\nconfirm = false\nmodel = "claude-sonnet-5-5"\n')
    loaded = load()
    assert (loaded.settings.ai_enabled, loaded.settings.ai_confirm) == (False, False)
    assert loaded.settings.ai_model == "claude-sonnet-5-5"
    assert loaded.sources["ai.enabled"] == "file"


@pytest.mark.parametrize("word", ["off", "false", "no", "0", "OFF"])
def test_sijl_ai_off_turns_ai_off(word: str) -> None:
    loaded = load(env={"SIJL_AI": word})
    assert loaded.settings.ai_enabled is False
    assert loaded.sources["ai.enabled"] == "env SIJL_AI"


def test_flags_no_ai_and_yes() -> None:
    config_file("[ai]\nenabled = true\nconfirm = true\n")
    loaded = load(ai_enabled=False, ai_confirm=False)
    assert (loaded.settings.ai_enabled, loaded.settings.ai_confirm) == (False, False)
    assert loaded.sources["ai.enabled"] == "flag --no-ai"
    assert loaded.sources["ai.confirm"] == "flag --yes"


def test_the_model_cannot_be_empty() -> None:
    config_file('[ai]\nmodel = "  "\n')
    with pytest.raises(ConfigError, match=r"ai\.model"):
        load()


@pytest.mark.parametrize("key", ["api_key", "auth_token", "anthropic_api_key"])
def test_a_claude_key_in_the_file_is_refused(key: str) -> None:
    config_file(f'[ai]\n{key} = "sk-ant-not-a-key"\n')
    with pytest.raises(ConfigError, match="never reads secrets from the config file"):
        load()


def test_an_unknown_ai_key_gets_a_suggestion() -> None:
    config_file("[ai]\nenable = false\n")
    with pytest.raises(ConfigError, match=r"did you mean 'ai\.enabled'"):
        load()
