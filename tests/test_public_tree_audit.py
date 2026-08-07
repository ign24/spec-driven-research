import subprocess
import sys
from pathlib import Path

import pytest

from sdr.public_tree_audit import (
    EVALUATION_CATEGORY,
    PUBLIC_CATEGORIES,
    Finding,
    audit_tree,
    public_category,
    redact_sensitive,
    redact_sensitive_values,
    render_findings,
)


@pytest.mark.parametrize(
    "relative_path",
    [
        "research/example/brief.md",
        "knowledge/example.md",
        "analysis.ipynb",
        ".env",
        ".env.production",
        "src/__pycache__/module.pyc",
        ".pytest_cache/state",
        ".ruff_cache/state",
        "vendor/.git/config",
    ],
)
def test_audit_rejects_prohibited_paths(tmp_path, relative_path):
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("public content", encoding="utf-8")

    findings = audit_tree(tmp_path)

    assert any(finding.code == "prohibited-path" for finding in findings)


def test_root_git_can_be_excluded_without_hiding_nested_git(tmp_path):
    root_git = tmp_path / ".git" / "config"
    nested_git = tmp_path / "artifact" / ".git" / "config"
    root_git.parent.mkdir()
    nested_git.parent.mkdir(parents=True)
    root_git.write_text("private root metadata", encoding="utf-8")
    nested_git.write_text("embedded repository", encoding="utf-8")

    findings = audit_tree(tmp_path, excluded=(Path(".git"),))

    assert [finding.path for finding in findings] == ["artifact/.git"]


def test_sensitive_text_is_reported_without_echoing_values(tmp_path):
    private_path = "/" + "home/example-user/private/source/file.py"
    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    github_token = "ghp_" + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"
    target = tmp_path / "config.txt"
    target.write_text(
        f"origin={private_path}\naws={aws_key}\ngithub={github_token}\n",
        encoding="utf-8",
    )

    findings = audit_tree(tmp_path)
    output = render_findings(findings)

    assert {finding.code for finding in findings} == {
        "private-absolute-path",
        "secret",
    }
    assert private_path not in output
    assert aws_key not in output
    assert github_token not in output
    assert "config.txt:1" in output


def test_sensitive_filename_is_redacted(tmp_path):
    github_token = "ghp_" + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"
    (tmp_path / github_token).write_text(github_token, encoding="utf-8")

    output = render_findings(audit_tree(tmp_path))

    assert github_token not in output
    assert "<redacted-value-1>" in output


def test_payload_redaction_uses_opaque_first_encounter_labels_without_candidate_oracle():
    published_secret = "published-secret-value"
    other_secret = "different-secret-value"

    payload = redact_sensitive_values(
        {
            "first": f"TOKEN={published_secret}",
            "repeated": f"PASSWORD={published_secret}",
            "distinct": f"SECRET={other_secret}",
        }
    )
    published_marker = payload["first"].split("=", 1)[1]

    assert payload == {
        "first": "TOKEN=<redacted-value-1>",
        "repeated": "PASSWORD=<redacted-value-1>",
        "distinct": "SECRET=<redacted-value-2>",
    }
    assert redact_sensitive(f"TOKEN={published_secret}").split("=", 1)[1] == published_marker
    assert redact_sensitive("TOKEN=incorrect-candidate").split("=", 1)[1] == published_marker
    assert published_secret not in repr(payload)
    assert other_secret not in repr(payload)
    assert "sha256" not in repr(payload).casefold()


@pytest.mark.parametrize("marker_first", [False, True])
def test_payload_redaction_preserves_marker_shaped_sensitive_input_without_aliasing(marker_first):
    marker_input = "<redacted-value-1>"
    entries = [
        ("actual", "TOKEN=actual-secret"),
        ("marker_shaped", f"TOKEN={marker_input}"),
    ]
    if marker_first:
        entries.reverse()

    payload = redact_sensitive_values(dict(entries))
    actual_label = payload["actual"].split("=", 1)[1]
    marker_label = payload["marker_shaped"].split("=", 1)[1]

    assert actual_label != marker_label
    assert marker_label == marker_input
    assert "actual-secret" not in repr(payload)


@pytest.mark.parametrize("marker_first", [False, True])
def test_payload_redaction_preserves_marker_shaped_path_without_aliasing(marker_first):
    marker_input = "<redacted-value-1>"
    private_path = "/" + "home/alice/private/source.txt"
    values = [private_path, marker_input]
    if marker_first:
        values.reverse()

    payload = redact_sensitive_values(values)

    assert len(set(payload)) == 2
    assert payload[values.index(marker_input)] == marker_input
    assert private_path not in payload


def test_payload_redaction_reserves_sparse_markers_and_is_idempotent():
    payload = {
        "out_of_order": ["<redacted-value-7>", "<redacted-value-2>"],
        "raw": ["TOKEN=alpha-secret", "PASSWORD=alpha-secret", "SECRET=beta-secret"],
        "malformed": ["<redacted-value-0>", "<redacted-value-01>", "<redacted-value-x>"],
    }

    first = redact_sensitive_values(payload)
    second = redact_sensitive_values(first)
    third = redact_sensitive_values(second)

    assert first == second == third
    assert first["out_of_order"] == ["<redacted-value-7>", "<redacted-value-2>"]
    assert first["raw"] == [
        "TOKEN=<redacted-value-1>",
        "PASSWORD=<redacted-value-1>",
        "SECRET=<redacted-value-3>",
    ]
    assert first["malformed"] == [
        "<redacted-value-0>",
        "<redacted-value-01>",
        "<redacted-value-x>",
    ]


def test_payload_redaction_treats_malformed_marker_like_sensitive_values_as_raw():
    payload = [
        "TOKEN=<redacted-value-0>",
        "PASSWORD=<redacted-value-01>",
        "SECRET=<redacted-value-x>",
    ]

    redacted = redact_sensitive_values(payload)

    assert redacted == [
        "TOKEN=<redacted-value-1>",
        "PASSWORD=<redacted-value-2>",
        "SECRET=<redacted-value-3>",
    ]


def test_findings_have_deterministic_order(tmp_path):
    private_path = "/" + "Users/person/private/file"
    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    (tmp_path / "z.txt").write_text(f"path={private_path}\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text(f"token={aws_key}\n", encoding="utf-8")

    first = audit_tree(tmp_path)
    second = audit_tree(tmp_path)

    assert first == second
    assert [(item.path, item.line) for item in first] == sorted(
        (item.path, item.line) for item in first
    )


def test_module_cli_audits_arbitrary_artifact_root(tmp_path):
    artifact = tmp_path / "dist"
    artifact.mkdir()
    (artifact / "bundle.env").write_text("safe=true\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "sdr.public_tree_audit", str(artifact)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "prohibited-path" in result.stdout
    assert str(tmp_path) not in result.stdout


def test_safe_tree_passes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert audit_tree(tmp_path) == []


@pytest.mark.parametrize(
    "relative_path",
    ["bench", "bench/corpus/clean-light/item.yaml", "bench/harness/runspace.py"],
)
def test_evaluation_corpus_and_harness_are_a_documented_public_category(relative_path):
    assert PUBLIC_CATEGORIES["bench"] == EVALUATION_CATEGORY
    assert public_category(Path(relative_path)) == EVALUATION_CATEGORY


def test_paths_outside_a_documented_category_have_no_category():
    assert public_category(Path("scratch/output.json")) is None


def test_evaluation_tree_passes_the_public_audit(tmp_path):
    harness = tmp_path / "bench" / "harness"
    corpus = tmp_path / "bench" / "corpus" / "clean-light"
    harness.mkdir(parents=True)
    corpus.mkdir(parents=True)
    (harness / "runspace.py").write_text("ROOT = 'bench'\n", encoding="utf-8")
    (corpus / "item.yaml").write_text("id: clean-light\nmode: light\n", encoding="utf-8")

    assert audit_tree(tmp_path) == []


def test_harness_lifecycle_metadata_in_the_tree_is_reported(tmp_path):
    run_output = tmp_path / "bench" / "runs" / "clean-light-1"
    run_output.mkdir(parents=True)
    (run_output / "sdr.yaml").write_text("slug: clean-light\n", encoding="utf-8")

    findings = audit_tree(tmp_path)

    assert findings == [Finding("harness-residue", "bench/runs/clean-light-1/sdr.yaml")]


def test_lifecycle_metadata_outside_the_evaluation_category_is_not_harness_residue(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "sdr.yaml").write_text("slug: example\n", encoding="utf-8")

    assert audit_tree(tmp_path) == []
