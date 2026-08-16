import importlib.util
import json

import pytest
from click.testing import CliRunner

from sdr.cli import main


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))
    runner = CliRunner()
    return lambda *args: runner.invoke(main, list(args), catch_exceptions=False)


def test_semantic_judge_module_is_removed():
    assert importlib.util.find_spec("sdr.judge") is None


def test_judge_tombstone_is_actionable_and_does_not_echo_deprecated_values(run, monkeypatch):
    secret = "provider-secret-must-not-leak"
    monkeypatch.setenv("SDR_JUDGE_PROVIDER", secret)
    monkeypatch.setenv("SDR_JUDGE_CMD", f"missing-command {secret}")

    result = run("judge", "legacy-slug", "--stage", "explore", "--json")

    assert result.exit_code != 0
    assert "retired" in result.output.lower()
    assert "verify-claims" in result.output
    assert "resolve-claim" in result.output
    assert secret not in result.output
    assert "does not exist" not in result.output


def test_doctor_is_general_and_reports_only_deprecated_variable_names(run, monkeypatch):
    secret = "doctor-secret-must-not-leak"
    monkeypatch.setenv("SDR_JUDGE_PROVIDER", secret)
    monkeypatch.setenv("SDR_JUDGE_CMD", secret)
    monkeypatch.setenv("SDR_JUDGE_UNRECOGNIZED_LEGACY_NAME", secret)

    result = run("doctor", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "ready": True,
        "deprecated_environment": [
            "SDR_JUDGE_CMD",
            "SDR_JUDGE_PROVIDER",
            "SDR_JUDGE_UNRECOGNIZED_LEGACY_NAME",
        ],
    }
    assert secret not in result.output
    assert "judge" not in payload


def test_doctor_succeeds_without_model_configuration(run, monkeypatch):
    for name in (
        "SDR_JUDGE_CMD",
        "SDR_JUDGE_MODEL",
        "SDR_JUDGE_PROVIDER",
        "SDR_JUDGE_MAX_TOKENS",
        "SDR_JUDGE_TEMPERATURE",
    ):
        monkeypatch.delenv(name, raising=False)
    result = run("doctor", "--json")

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ready": True, "deprecated_environment": []}
