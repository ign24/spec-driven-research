import importlib.resources
import shutil
import tomllib
from pathlib import Path

import yaml

from sdr.integration_validation import (
    ADAPTERS,
    ROUTING_BLOCK_RESOURCE,
    ROUTING_BLOCK_TITLE,
    ROUTING_EXCLUDING_HEADING,
    ROUTING_SELECTING_HEADING,
    canonical_routing_block,
    validate_integrations,
    validate_routing_block,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy_public_tree(tmp_path: Path) -> Path:
    shutil.copytree(PROJECT_ROOT / "integrations", tmp_path / "integrations")
    shutil.copy(PROJECT_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy(PROJECT_ROOT / "README.md", tmp_path / "README.md")
    shutil.copy(PROJECT_ROOT / "README.es.md", tmp_path / "README.es.md")
    (tmp_path / "docs").mkdir()
    shutil.copy(PROJECT_ROOT / "docs" / "integrations.md", tmp_path / "docs" / "integrations.md")
    return tmp_path


def test_exactly_one_canonical_routing_block_ships_as_a_package_resource() -> None:
    resource = importlib.resources.files("sdr").joinpath(ROUTING_BLOCK_RESOURCE)

    assert resource.is_file()

    block = canonical_routing_block()

    assert block.strip()
    assert block.startswith(ROUTING_BLOCK_TITLE)

    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_dirs = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    authored = PROJECT_ROOT / "src" / "sdr" / ROUTING_BLOCK_RESOURCE

    assert "src/sdr" in package_dirs
    assert authored.is_file()
    assert authored.read_text(encoding="utf-8").strip() == block

    sources = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "src").rglob("*.md")
        if path.is_file() and ROUTING_BLOCK_TITLE in path.read_text(encoding="utf-8")
    )

    assert sources == [f"src/sdr/{ROUTING_BLOCK_RESOURCE}"]
    assert validate_integrations(PROJECT_ROOT) == []


def test_routing_block_must_state_excluding_conditions_not_only_selecting_ones() -> None:
    block = canonical_routing_block()

    assert validate_routing_block(block) == []
    assert ROUTING_SELECTING_HEADING in block
    assert ROUTING_EXCLUDING_HEADING in block

    one_sided = block.split(ROUTING_EXCLUDING_HEADING, 1)[0].rstrip() + "\n"
    findings = validate_routing_block(one_sided)

    assert any(
        finding.agent == "routing" and finding.code == "one-sided-routing-block"
        for finding in findings
    )

    without_selecting = block.replace(ROUTING_SELECTING_HEADING, "## Notes")
    assert any(
        finding.code == "one-sided-routing-block"
        for finding in validate_routing_block(without_selecting)
    )

    without_disclaimer = "\n".join(
        line for line in block.splitlines() if "guidance" not in line.casefold()
    )
    assert any(
        finding.code == "unenforceable-routing-block"
        for finding in validate_routing_block(without_disclaimer)
    )


def test_validator_identifies_a_divergent_or_missing_published_routing_block(
    tmp_path: Path,
) -> None:
    root = _copy_public_tree(tmp_path)
    block = canonical_routing_block()

    assert validate_integrations(root) == []

    codex_readme = root / "integrations" / "codex" / "README.md"
    divergent = block.replace(ROUTING_EXCLUDING_HEADING, ROUTING_EXCLUDING_HEADING + "\n- Never.")
    codex_readme.write_text(
        codex_readme.read_text(encoding="utf-8").replace(block, divergent), encoding="utf-8"
    )
    opencode_readme = root / "integrations" / "opencode" / "README.md"
    opencode_readme.write_text(
        opencode_readme.read_text(encoding="utf-8").replace(block, "See the adapter guide."),
        encoding="utf-8",
    )

    findings = validate_integrations(root)

    assert any(
        finding.agent == "codex" and finding.code == "divergent-routing-block"
        for finding in findings
    )
    assert any(
        finding.agent == "opencode" and finding.code == "missing-routing-block"
        for finding in findings
    )
    assert not any(finding.agent == "claude-code" for finding in findings)


def test_publishing_the_routing_block_leaves_adapter_statuses_documented() -> None:
    for agent in ADAPTERS:
        metadata = yaml.safe_load(
            (PROJECT_ROOT / "integrations" / agent / "adapter.yaml").read_text(encoding="utf-8")
        )
        readme = (PROJECT_ROOT / "integrations" / agent / "README.md").read_text(encoding="utf-8")

        assert metadata["status"] == "documented"
        assert metadata["status_evidence"].get("host_e2e") is None
        assert canonical_routing_block() in readme
