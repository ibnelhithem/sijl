"""`ai.answer_limit`: the longest answer `sijl ask` may get, in tokens — Claude's
thinking included. Lower than the API's 64,000 by default, so the preview's cost ceiling means
something; set like every other setting, and never named like a secret (the config file refuses
keys that look like one)."""

import pytest

from sijl import ai
from sijl.config import ConfigError
from tests.test_config import config_file, load


def test_the_default_is_lower_than_t23s_limit() -> None:
    loaded = load()
    assert loaded.settings.ai_answer_limit == ai.ANSWER_LIMIT == 16_000
    assert ai.ANSWER_LIMIT < ai.MAX_OUTPUT_TOKENS
    assert loaded.sources["ai.answer_limit"] == "default"


def test_file_environment_and_flag_set_it() -> None:
    config_file("[ai]\nanswer_limit = 8000\n")
    assert load().settings.ai_answer_limit == 8_000
    assert load(env={"SIJL_AI_ANSWER_LIMIT": "32000"}).settings.ai_answer_limit == 32_000
    loaded = load(ai_answer_limit=4_096)
    assert loaded.settings.ai_answer_limit == 4_096
    assert loaded.sources["ai.answer_limit"] == "flag --answer-limit"


@pytest.mark.parametrize("value", [0, 1_023, 128_001])
def test_a_limit_outside_what_claude_takes_is_refused(value: int) -> None:
    config_file(f"[ai]\nanswer_limit = {value}\n")
    with pytest.raises(ConfigError, match=r"ai\.answer_limit must be between 1,024 and 128,000"):
        load()
