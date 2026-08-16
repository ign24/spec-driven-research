import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
SPECS_ROOT = REPOSITORY_ROOT / "openspec" / "specs"
CAPABILITIES = {
    "public-repository-boundary",
    "python-distribution",
    "agent-integrations",
    "cross-investigation-reuse",
    "github-installation",
    "product-language",
    "public-documentation",
    "release-quality-and-security",
    "research-evaluation-harness",
    "sdr-lifecycle-evidence-contract",
}


def test_public_capability_specs_are_consolidated_and_verifiable():
    assert {path.name for path in SPECS_ROOT.iterdir() if path.is_dir()} == CAPABILITIES

    for capability in CAPABILITIES:
        spec = SPECS_ROOT / capability / "spec.md"
        text = spec.read_text(encoding="utf-8")
        assert "## Purpose" in text
        assert "## Requirements" in text
        assert "## ADDED Requirements" not in text
        requirements = list(re.finditer(r"^### Requirement: .+$", text, re.MULTILINE))
        scenarios = list(re.finditer(r"^#### Scenario: .+$", text, re.MULTILINE))
        assert requirements, capability
        assert len(scenarios) >= len(requirements), capability
        section_ends = [match.start() for match in requirements[1:]] + [len(text)]
        for requirement, section_end in zip(requirements, section_ends, strict=True):
            section = text[requirement.start() : section_end]
            assert "- **WHEN**" in section, requirement.group()
            assert "- **THEN**" in section, requirement.group()


def test_specs_do_not_include_extraction_history_or_private_workspace_paths():
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SPECS_ROOT.rglob("*.md"))
    )

    assert "extract-sdr-open-source-repository" not in combined
    assert "/home/" not in combined
    assert "private working tree" not in combined.lower()
