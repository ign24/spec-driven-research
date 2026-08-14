import importlib.resources
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from sdr import __version__
from sdr.integration_validation import (
    ADAPTERS,
    canonical_routing_block,
    install_canonical_skills,
    validate_integrations,
)
from sdr.skill_validation import SKILLS

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _metadata(agent: str) -> dict[str, object]:
    path = PROJECT_ROOT / "integrations" / agent / "adapter.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _copy_integrations(tmp_path: Path) -> Path:
    shutil.copytree(PROJECT_ROOT / "integrations", tmp_path / "integrations")
    shutil.copy(PROJECT_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy(PROJECT_ROOT / "README.md", tmp_path / "README.md")
    shutil.copy(PROJECT_ROOT / "README.es.md", tmp_path / "README.es.md")
    (tmp_path / "docs").mkdir()
    shutil.copy(PROJECT_ROOT / "docs" / "integrations.md", tmp_path / "docs" / "integrations.md")
    return tmp_path


def _replace_public_status(root: Path, agent_name: str, status: str) -> None:
    for relative_path in ("README.md", "README.es.md", "docs/integrations.md"):
        path = root / relative_path
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text(
            "\n".join(
                line.replace("`documented`", f"`{status}`")
                if line.startswith(f"| {agent_name} |")
                else line
                for line in lines
            )
            + "\n",
            encoding="utf-8",
        )


def _canary_evidence(root: Path) -> tuple[Path, dict[str, object]]:
    path = root / "integrations" / "canary-evidence.json"
    if path.is_file():
        return path, json.loads(path.read_text(encoding="utf-8"))

    def adapter(discovery: str) -> dict[str, object]:
        return {
            "host_version": "1.2.3",
            "policy": {
                "authentication": "no-credentials",
                "model": "not-invoked",
                "network": "disabled",
            },
            "discovery": {"result": discovery},
            "lifecycle": {"result": "not-run"},
            "limitations": ["No host-driven SDR lifecycle was run."],
        }

    return path, {
        "schema_version": 1,
        "recorded_on": "2026-07-28",
        "artifact": {
            "package_version": __version__,
            "wheel_filename": f"spec_driven_research-{__version__}-py3-none-any.whl",
            "sha256": "a" * 64,
        },
        "environment": {
            "isolation": "env-i",
            "home": "temporary",
            "workspace": "temporary-outside-checkout",
            "python_version": "3.13.5",
            "os": "Linux",
            "architecture": "x86_64",
        },
        "adapters": {
            "claude-code": adapter("filesystem-only"),
            "codex": adapter("discovered"),
            "opencode": adapter("discovered"),
        },
    }


def _write_canary_evidence(path: Path, evidence: dict[str, object]) -> None:
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def _write_packaged_skills(package_root: Path, *, omit: str | None = None) -> dict[str, bytes]:
    expected: dict[str, bytes] = {}
    for name in SKILLS:
        content = f"canonical bytes for {name}\n".encode()
        expected[name] = content
        if name == omit:
            continue
        skill = package_root / "resources" / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_bytes(content)
    return expected


def test_supported_adapters_are_exactly_three_and_documented() -> None:
    assert ADAPTERS == (
        "claude-code",
        "opencode",
        "codex",
    )
    assert validate_integrations(PROJECT_ROOT) == []
    assert {
        path.name for path in (PROJECT_ROOT / "integrations").iterdir() if path.is_dir()
    } == set(ADAPTERS)

    for agent in ADAPTERS:
        adapter_dir = PROJECT_ROOT / "integrations" / agent
        assert (adapter_dir / "README.md").is_file()
        assert (adapter_dir / "adapter.yaml").is_file()
        assert _metadata(agent)["status"] == "documented"


def test_validator_requires_sanitized_machine_readable_canary_evidence(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    evidence_path, evidence = _canary_evidence(root)
    evidence_path.unlink(missing_ok=True)

    findings = validate_integrations(root)

    assert any(finding.code == "missing-canary-evidence" for finding in findings)

    _write_canary_evidence(evidence_path, evidence)
    evidence["artifact"]["sha256"] = "not-a-digest"
    evidence["environment"]["workspace"] = "/" + "home/example/private"
    _write_canary_evidence(evidence_path, evidence)

    findings = validate_integrations(root)

    assert any(finding.code == "invalid-canary-evidence" for finding in findings)
    assert any(finding.code == "unsafe-canary-evidence" for finding in findings)


def test_validator_rejects_missing_or_additional_canary_adapters(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    evidence_path, evidence = _canary_evidence(root)
    evidence["adapters"].pop("codex")
    evidence["adapters"]["other-agent"] = evidence["adapters"]["opencode"]
    _write_canary_evidence(evidence_path, evidence)

    findings = validate_integrations(root)

    assert any(
        finding.agent == "codex" and finding.code == "missing-canary-adapter"
        for finding in findings
    )
    assert any(
        finding.agent == "other-agent" and finding.code == "unexpected-canary-adapter"
        for finding in findings
    )


def test_validator_rejects_malformed_nested_canary_evidence_without_crashing(
    tmp_path: Path,
) -> None:
    root = _copy_integrations(tmp_path)
    evidence_path, evidence = _canary_evidence(root)
    evidence["adapters"]["codex"]["policy"] = "unknown"
    evidence["adapters"]["opencode"] = []
    _write_canary_evidence(evidence_path, evidence)

    findings = validate_integrations(root)

    assert {finding.agent for finding in findings if finding.code == "invalid-canary-evidence"} >= {
        "codex",
        "opencode",
    }


def test_validator_rejects_canary_evidence_that_overstates_status(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    evidence_path, evidence = _canary_evidence(root)
    evidence["adapters"]["claude-code"]["discovery"]["result"] = "discovered"
    evidence["adapters"]["codex"]["lifecycle"]["result"] = "passed"
    evidence["adapters"]["opencode"]["policy"]["model"] = "invoked"
    _write_canary_evidence(evidence_path, evidence)

    findings = validate_integrations(root)

    assert {
        (finding.agent, finding.code)
        for finding in findings
        if finding.code == "overstated-canary-evidence"
    } == {
        ("claude-code", "overstated-canary-evidence"),
        ("codex", "overstated-canary-evidence"),
        ("opencode", "overstated-canary-evidence"),
    }


def test_validator_ties_public_canary_claims_to_evidence_record(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    evidence_path, evidence = _canary_evidence(root)
    _write_canary_evidence(evidence_path, evidence)
    guide = root / "docs" / "integrations.md"
    guide.write_text(
        guide.read_text(encoding="utf-8").replace("[sanitized canary evidence]", "canary evidence"),
        encoding="utf-8",
    )

    findings = validate_integrations(root)

    assert any(finding.code == "missing-canary-evidence-link" for finding in findings)


def test_adapters_reference_the_canonical_skills_without_runtime_dependencies() -> None:
    for agent in ADAPTERS:
        metadata = _metadata(agent)
        assert metadata["canonical_skills"] == "../../skills"
        assert metadata["skills"] == list(SKILLS)
        assert metadata["runtime_dependencies"] == []

        adapter_dir = PROJECT_ROOT / "integrations" / agent
        assert not any(path.name == "SKILL.md" for path in adapter_dir.rglob("SKILL.md"))


def test_agent_specific_discovery_and_safety_contracts() -> None:
    assert _metadata("claude-code")["discovery"]["project"] == ".claude/skills"

    opencode = _metadata("opencode")
    assert opencode["adapter"] == "sdr"
    assert opencode["discovery"]["project"] == ".opencode/skills"

    codex = _metadata("codex")
    assert codex["discovery"]["project"] == ".agents/skills"
    assert codex["installation"] == "package-resource-copy"


def test_validator_rejects_additional_adapter_directories(tmp_path: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "integrations", tmp_path / "integrations")
    (tmp_path / "integrations" / "other-agent").mkdir()

    findings = validate_integrations(tmp_path)

    assert any(
        finding.agent == "other-agent" and finding.code == "unexpected-adapter"
        for finding in findings
    )


def test_validator_rejects_unsupported_hatch_integration_mapping(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    descriptor = root / "integrations" / "other-agent" / "adapter.yaml"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("agent: other-agent\n", encoding="utf-8")
    config = root / "pyproject.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            '"integrations/opencode/adapter.yaml" = '
            '"sdr/resources/integrations/opencode/adapter.yaml"',
            '"integrations/opencode/adapter.yaml" = '
            '"sdr/resources/integrations/opencode/adapter.yaml"\n'
            '"integrations/other-agent/adapter.yaml" = '
            '"sdr/resources/integrations/other-agent/adapter.yaml"',
        ),
        encoding="utf-8",
    )

    findings = validate_integrations(root)

    assert any(
        finding.agent == "other-agent" and finding.code == "unexpected-packaged-adapter"
        for finding in findings
    )


def test_validator_rejects_additional_public_status_table_rows(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            "| OpenCode | `sdr integrations install --destination .opencode/skills` | `documented` |",
            "| OpenCode | `sdr integrations install --destination .opencode/skills` | `documented` |\n"
            "| Other Agent | `sdr integrations install --destination .other/skills` | `documented` |",
        ),
        encoding="utf-8",
    )

    findings = validate_integrations(root)

    assert any(
        finding.agent == "other-agent" and finding.code == "unexpected-public-adapter"
        for finding in findings
    )


def test_validator_requires_public_status_tables_to_match_descriptors(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    guide = root / "docs" / "integrations.md"
    guide.write_text(
        guide.read_text(encoding="utf-8").replace(
            "| Codex | Repository or user Agent Skills directory | `documented` | No |",
            "| Codex | Repository or user Agent Skills directory | `experimental` | No |",
        ),
        encoding="utf-8",
    )

    findings = validate_integrations(root)

    assert any(
        finding.agent == "codex"
        and finding.code == "public-status"
        and "docs/integrations.md" in finding.detail
        for finding in findings
    )


def test_install_copies_all_packaged_skills_as_regular_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "package"
    expected = _write_packaged_skills(package_root)
    monkeypatch.setattr(importlib.resources, "files", lambda package: package_root)
    destination = tmp_path / ".agents" / "skills"

    installed = install_canonical_skills(destination)

    assert [path.name for path in installed] == list(SKILLS)
    for name in SKILLS:
        skill_dir = destination / name
        skill_file = skill_dir / "SKILL.md"
        assert skill_dir.is_dir() and not skill_dir.is_symlink()
        assert skill_file.is_file() and not skill_file.is_symlink()
        assert skill_file.read_bytes() == expected[name]

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        install_canonical_skills(destination)


def test_install_preflights_every_resource_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "package"
    missing = SKILLS[-1]
    _write_packaged_skills(package_root, omit=missing)
    monkeypatch.setattr(importlib.resources, "files", lambda package: package_root)
    destination = tmp_path / ".agents" / "skills"

    with pytest.raises(FileNotFoundError, match=f"canonical skill is missing: {missing}"):
        install_canonical_skills(destination)

    assert not destination.exists()


def test_install_preflights_all_conflicts_and_preserves_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "package"
    _write_packaged_skills(package_root)
    monkeypatch.setattr(importlib.resources, "files", lambda package: package_root)
    destination = tmp_path / ".agents" / "skills"
    destination.mkdir(parents=True)
    unrelated = destination / "host-config.json"
    unrelated.write_bytes(b'{"untouched": true}\n')
    conflict = destination / SKILLS[-1]
    conflict.symlink_to(tmp_path / "missing-skill")

    with pytest.raises(
        FileExistsError, match=f"refusing to overwrite existing skills: {SKILLS[-1]}"
    ):
        install_canonical_skills(destination)

    assert conflict.is_symlink() and not conflict.exists()
    assert unrelated.read_bytes() == b'{"untouched": true}\n'
    assert not any((destination / name).exists() for name in SKILLS[:-1])


def test_adapter_guides_keep_research_root_separate_from_installation_source() -> None:
    for agent in ADAPTERS:
        text = (PROJECT_ROOT / "integrations" / agent / "README.md").read_text(encoding="utf-8")
        assert "sdr integrations install --destination" in text
        assert 'install "$SDR_ROOT"' not in text
        assert "$SDR_ROOT/skills" not in text
        assert "research" in text.casefold()


def test_documented_status_requires_explicit_absence_of_host_e2e(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    metadata_path = root / "integrations" / "codex" / "adapter.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    metadata["status_evidence"] = {"schema_version": 1, "host_e2e": {"result": "passed"}}
    metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    findings = validate_integrations(root)

    assert any(
        finding.agent == "codex" and finding.code == "status-evidence" for finding in findings
    )


def test_verified_status_requires_complete_version_matched_host_e2e(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    metadata_path = root / "integrations" / "opencode" / "adapter.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    metadata["status"] = "verified"
    metadata["status_evidence"] = {
        "schema_version": 1,
        "host_e2e": {
            "host_version": "1.2.3",
            "sdr_version": "different-version",
            "acquisition_identity": "sha256:" + "a" * 64,
            "environment": "isolated Linux sandbox",
            "discovery": "passed",
            "lifecycle": "passed",
        },
    }
    metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    findings = validate_integrations(root)

    assert any(
        finding.agent == "opencode" and finding.code == "status-evidence" for finding in findings
    )


def test_verified_status_accepts_complete_version_matched_host_e2e(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    metadata_path = root / "integrations" / "opencode" / "adapter.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    metadata["status"] = "verified"
    metadata["status_evidence"] = {
        "schema_version": 1,
        "host_e2e": {
            "host_version": "1.2.3",
            "sdr_version": __version__,
            "acquisition_identity": "sha256:" + "a" * 64,
            "environment": "isolated Linux sandbox",
            "discovery": "passed",
            "lifecycle": "passed",
        },
    }
    metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    _replace_public_status(root, "OpenCode", "verified")

    assert validate_integrations(root) == []


def test_validator_rejects_divergent_or_unsafe_adapter_content(tmp_path: Path) -> None:
    integrations = tmp_path / "integrations"
    source = PROJECT_ROOT / "integrations"
    shutil.copytree(source, integrations)
    readme = integrations / "opencode" / "README.md"
    private_path = "/" + "home/example/private"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        + f"\nUse SpecLab from {private_path} with model gpt-5.\n"
        + "Run sdr deploy and follow intake -> transfer.\n",
        encoding="utf-8",
    )

    findings = validate_integrations(tmp_path)

    assert {finding.code for finding in findings} >= {
        "fixed-model",
        "legacy-concept",
        "private-path",
        "unknown-command",
        "divergent-lifecycle",
    }


def test_validator_reports_routing_block_findings_per_adapter(tmp_path: Path) -> None:
    root = _copy_integrations(tmp_path)
    block = canonical_routing_block()
    readme = root / "integrations" / "claude-code" / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(block, block + "\n\n" + block),
        encoding="utf-8",
    )

    findings = validate_integrations(root)

    assert any(
        finding.agent == "claude-code" and finding.code == "duplicate-routing-block"
        for finding in findings
    )


def test_module_cli_validates_source_checkout(tmp_path: Path) -> None:
    validation = subprocess.run(
        [sys.executable, "-m", "sdr.integration_validation", "validate", str(PROJECT_ROOT)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert validation.returncode == 0
    assert validation.stdout == "integrations: OK\n"


def test_installed_wheel_cli_installs_atomically_outside_checkout(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist), str(PROJECT_ROOT)],
        check=True,
    )
    wheel = next(dist.glob("spec_driven_research-*.whl"))
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True)
    python = venv / "bin" / "python"
    subprocess.run(["uv", "pip", "install", "--python", str(python), str(wheel)], check=True)

    workspace = tmp_path / "outside-checkout"
    workspace.mkdir()
    destination = tmp_path / ".claude" / "skills"
    research_root = tmp_path / "research"
    unrelated_config = workspace / "agent-config.json"
    unrelated_config.write_bytes(b'{"enabled": true}\n')
    env = {**os.environ, "SDR_ROOT": str(research_root)}
    env.pop("PYTHONPATH", None)
    destination.mkdir(parents=True)
    conflict = destination / SKILLS[-1]
    conflict.symlink_to(tmp_path / "missing-skill")
    command = [
        str(python),
        "-m",
        "sdr.integration_validation",
        "install",
        "--destination",
        str(destination),
    ]

    refused = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        cwd=workspace,
        env=env,
    )

    assert refused.returncode != 0
    assert "refusing to overwrite existing skills" in refused.stderr
    assert not any((destination / name).exists() for name in SKILLS[:-1])
    assert conflict.is_symlink() and not conflict.exists()
    assert unrelated_config.read_bytes() == b'{"enabled": true}\n'
    assert not research_root.exists()

    conflict.unlink()
    installation = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        cwd=workspace,
        env=env,
    )

    assert installation.returncode == 0
    assert installation.stdout == "installed 7 canonical skills\n"
    for name in SKILLS:
        skill_dir = destination / name
        skill_file = skill_dir / "SKILL.md"
        assert skill_dir.is_dir() and not skill_dir.is_symlink()
        assert skill_file.is_file() and not skill_file.is_symlink()
        assert skill_file.read_bytes() == (PROJECT_ROOT / "skills" / name / "SKILL.md").read_bytes()
    assert not research_root.exists()
    assert unrelated_config.read_bytes() == b'{"enabled": true}\n'


def test_uv_tool_installed_sdr_command_installs_seven_regular_skills_outside_checkout(
    tmp_path: Path,
) -> None:
    tool_dir = tmp_path / "uv-tools"
    bin_dir = tmp_path / "uv-bin"
    install_env = {
        **os.environ,
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_TOOL_DIR": str(tool_dir),
        "UV_TOOL_BIN_DIR": str(bin_dir),
    }
    install_env.pop("PYTHONPATH", None)
    subprocess.run(
        ["uv", "tool", "install", "."],
        check=True,
        cwd=PROJECT_ROOT,
        env=install_env,
    )

    workspace = tmp_path / "outside-checkout"
    workspace.mkdir()
    destination = workspace / ".agents" / "skills"
    research_root = workspace / "research"
    agent_config = workspace / "agent-config.json"
    agent_config.write_bytes(b'{"untouched": true}\n')
    command_env = {**install_env, "SDR_ROOT": str(research_root)}
    command = [
        str(bin_dir / "sdr"),
        "integrations",
        "install",
        "--destination",
        str(destination),
        "--json",
    ]
    destination.mkdir(parents=True)
    conflict = destination / SKILLS[-1]
    conflict.symlink_to(workspace / "missing-skill")
    refused = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        cwd=workspace,
        env=command_env,
    )

    assert refused.returncode != 0
    assert "refusing to overwrite existing skills" in refused.stderr
    assert not any((destination / name).exists() for name in SKILLS[:-1])
    assert conflict.is_symlink() and not conflict.exists()
    assert not research_root.exists()
    assert agent_config.read_bytes() == b'{"untouched": true}\n'

    conflict.unlink()
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        cwd=workspace,
        env=command_env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "destination": str(destination),
        "installed": list(SKILLS),
        "installed_count": 7,
    }
    assert {path.name for path in destination.iterdir()} == set(SKILLS)
    for name in SKILLS:
        skill_dir = destination / name
        skill_file = skill_dir / "SKILL.md"
        assert skill_dir.is_dir() and not skill_dir.is_symlink()
        assert skill_file.is_file() and not skill_file.is_symlink()
    assert not research_root.exists()
    assert agent_config.read_bytes() == b'{"untouched": true}\n'
