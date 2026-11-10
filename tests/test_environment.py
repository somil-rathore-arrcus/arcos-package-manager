"""Reading the deployment's environment: clear failures, and .env as Compose reads it."""

from __future__ import annotations

import pytest

from apm import config
from apm.config import Environment


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No real .env and no APM_ variables from the machine running the tests."""
    import os

    for name in list(os.environ):
        if name.startswith("APM_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    return monkeypatch


def test_defaults_apply_when_numbers_are_unset_or_empty(clean_env):
    clean_env.setenv("APM_CACHE_TTL", "")
    env = Environment.from_env()
    assert (env.cache_ttl, env.git_long_timeout, env.ssh.port) == (86400, 900, None)


def test_a_bad_number_names_the_variable(clean_env):
    clean_env.setenv("APM_GIT_TIMEOUT", "90s")
    with pytest.raises(ValueError, match="APM_GIT_TIMEOUT='90s' is not a whole number"):
        Environment.from_env()


def test_dotenv_quotes_are_not_part_of_the_value(clean_env, tmp_path):
    (tmp_path / ".env").write_text(
        'APM_GITHUB_TOKEN="ghp_example"\n'
        "APM_API_TOKEN='s3cret'\n"
        'APM_COMMITTER_NAME=Somil "S" Rathore\n'
    )
    env = Environment.from_env()
    assert env.github_token == "ghp_example"
    assert env.api_token == "s3cret"
    assert env.committer_name == 'Somil "S" Rathore'


def test_the_api_token_is_reported_only_as_set(clean_env):
    clean_env.setenv("APM_API_TOKEN", "s3cret")
    redacted = Environment.from_env().redacted()
    assert redacted["api_token_set"] is True
    assert "s3cret" not in str(redacted)


@pytest.mark.parametrize("value,allowed", [
    (None, False), ("", False), ("0", False), ("no", False), ("maybe", False),
    ("1", True), ("true", True), ("YES", True), ("on", True),
])
def test_pushing_is_off_unless_this_machine_says_so(clean_env, value, allowed):
    """APM_ALLOW_PUSH is set only on the machine that holds the ARCoS access."""
    if value is not None:
        clean_env.setenv("APM_ALLOW_PUSH", value)
    env = Environment.from_env()
    assert env.allow_push is allowed
    assert env.redacted()["allow_push"] is allowed


def test_the_workspaces_carry_the_push_switch(clean_env):
    from apm.config import Settings
    from apm.services.container import build_workspaces

    for allowed in (False, True):
        env = Environment(allow_push=allowed)
        settings = Settings({})
        workspaces = build_workspaces(env, settings, env.transports(settings))
        assert workspaces.allow_push is allowed
        assert workspaces.scratch("x").allow_push is allowed
