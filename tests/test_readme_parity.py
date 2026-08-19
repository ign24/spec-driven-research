import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from sdr.readme_parity import canonical_repository, validate_readme_parity

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REPOSITORY = "https://github.com/ign24/spec-driven-research"
PYPROJECT = f"""[project]
name = "spec-driven-research"

[project.urls]
Repository = "{CANONICAL_REPOSITORY}"
"""


ROOT_EN = """# SDR

[Español](README.es.md)

question -> evidence -> optional probe -> human-approved decision -> reusable asset
Alpha software installed from a source checkout. There is no GitHub release or PyPI release.
Use `uv tool install .`; package-index publication is unavailable.
SDR validates declared structure and local evidence; it does not prove source truth.
People remain responsible for source quality, safe execution, and the final recommendation.
`new`, `advance`, `reopen`, `drop`, and `archive` commit by default; use `--no-commit` otherwise.
| Claude Code | `documented` |
| Codex | `documented` |
| OpenCode | `documented` |
[Docs](docs/README.md)
[Start](docs/getting-started.md)
"""
ROOT_ES = """# SDR

[English](README.md)

pregunta -> evidencia -> probe opcional -> decisión con aprobación humana -> activo reutilizable
Software alfa instalado desde un checkout del código fuente. No hay release en GitHub ni en PyPI.
Usa `python -m pip install .`; la publicación en índices de paquetes no está disponible.
SDR valida la estructura declarada y la evidencia local; no demuestra la verdad de las fuentes.
Las personas responden por la calidad de las fuentes, la ejecución segura y la recomendación final.
`new`, `advance`, `reopen`, `drop` y `archive` crean commits por defecto; usa `--no-commit` para evitarlos.
| Claude Code | `documented` |
| Codex | `documented` |
| OpenCode | `documented` |
[Documentación](docs/README.es.md)
[Inicio](docs/getting-started.es.md)
"""
HOME_EN = """# Documentation

[Español](README.es.md)
[Beginner operation](getting-started.md)
[Lifecycle](workflow.md)
[CLI](cli-reference.md)
[Evidence](evidence-model.md)
[Security](security-model.md)
[Integrations](integrations.md)
[Maintenance](validation.md)
[Releases](releasing.md)
"""
HOME_ES = """# Documentación

[English](README.md)
[Primeros pasos](getting-started.es.md)
[Ciclo](workflow.md)
[CLI](cli-reference.md)
[Evidencia](evidence-model.md)
[Seguridad](security-model.md)
[Integraciones](integrations.md)
[Mantenimiento](validation.md)
[Releases](releasing.md)
"""
START_EN = """# Getting started

[Español](getting-started.es.md)
Use only the maintained [synthetic light fixture](../examples/light-complete/README.md).
The offline path runs `intake -> explore -> transfer -> reuse -> done`; no probe is run.
Transfer needs explicit `sdr approve`; reuse remains mandatory.
Lifecycle commands commit by default. Pass `--no-commit` when Git is externally owned.
[Brief](../examples/light-complete/brief.md)
[Notes](../examples/light-complete/notes/landscape.md)
[Decision](../examples/light-complete/decision-memo.md)
[Asset](../examples/light-complete/assets/checklist.md)
"""
START_ES = """# Primeros pasos

[English](getting-started.md)
Usa solamente el [ejemplo light sintético mantenido](../examples/light-complete/README.md).
El recorrido sin red sigue `intake -> explore -> transfer -> reuse -> done`; no ejecuta probes.
Transfer exige `sdr approve` explícito y reuse sigue siendo obligatorio.
Los comandos del ciclo crean commits por defecto. Pasa `--no-commit` si otro actor gestiona Git.
[Brief](../examples/light-complete/brief.md)
[Notas](../examples/light-complete/notes/landscape.md)
[Decisión](../examples/light-complete/decision-memo.md)
[Activo](../examples/light-complete/assets/checklist.md)
"""


def _write_contract_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    files = {
        "README.md": (PROJECT_ROOT / "README.md").read_text(encoding="utf-8") + ROOT_EN,
        "README.es.md": (PROJECT_ROOT / "README.es.md").read_text(encoding="utf-8") + ROOT_ES,
        "docs/README.md": HOME_EN,
        "docs/README.es.md": HOME_ES,
        "docs/getting-started.md": START_EN,
        "docs/getting-started.es.md": START_ES,
        "docs/workflow.md": "# Workflow\n",
        "docs/cli-reference.md": "# CLI\n",
        "docs/evidence-model.md": "# Evidence\n",
        "docs/security-model.md": "# Security\n",
        "docs/integrations.md": "# Integrations\n",
        "docs/validation.md": "# Validation\n",
        "docs/releasing.md": "The repository does not publish to PyPI. Publishing is not enabled.\n",
        "pyproject.toml": PYPROJECT,
        "AGENTS.md": "# Agents\n",
        "CONTRIBUTING.md": "# Contributing\n",
        "SECURITY.md": "# Security\n",
        "LICENSE": "MIT\n",
        "examples/light-complete/README.md": "# Synthetic fixture\n",
        "examples/light-complete/brief.md": "# Brief\n",
        "examples/light-complete/notes/landscape.md": "# Notes\n",
        "examples/light-complete/decision-memo.md": "# Decision\n",
        "examples/light-complete/assets/checklist.md": "# Asset\n",
    }
    for filename, text in files.items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _copy_readmes(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    for filename in ("README.md", "README.es.md", "pyproject.toml"):
        shutil.copy2(PROJECT_ROOT / filename, root / filename)
    return root


def test_english_and_spanish_readmes_pass_semantic_parity_contract() -> None:
    assert validate_readme_parity(PROJECT_ROOT) == []


def test_all_three_bilingual_pairs_accept_equivalent_nonliteral_translations(
    tmp_path: Path,
) -> None:
    root = _write_contract_project(tmp_path)

    assert validate_readme_parity(root) == []


def test_validator_reports_missing_public_document_actionably(tmp_path: Path) -> None:
    root = _write_contract_project(tmp_path)
    (root / "docs" / "getting-started.es.md").unlink()

    findings = validate_readme_parity(root)

    assert any(
        item.code == "missing-document"
        and item.path == "docs/getting-started.es.md"
        and "create docs/getting-started.es.md" in item.message
        for item in findings
    )


def test_validator_requires_reciprocal_links_for_each_pair(tmp_path: Path) -> None:
    root = _write_contract_project(tmp_path)
    spanish_home = root / "docs" / "README.es.md"
    spanish_home.write_text(HOME_ES.replace("[English](README.md)", "English"), encoding="utf-8")

    findings = validate_readme_parity(root)

    assert any(
        item.code == "language-link"
        and item.path == "docs/README.es.md"
        and "README.md" in item.message
        for item in findings
    )


@pytest.mark.parametrize(
    ("filename", "old", "new", "code", "missing"),
    (
        ("README.md", "optional probe", "probe", "outcome-chain", "optional probe"),
        (
            "docs/getting-started.md",
            "intake -> explore -> transfer -> reuse -> done",
            "intake -> explore -> done",
            "light-lifecycle",
            "transfer",
        ),
        (
            "docs/getting-started.md",
            "commit by default",
            "changes files",
            "git-side-effects",
            "commit by default",
        ),
        (
            "README.md",
            "`new`, `advance`, `reopen`, `drop`, and `archive` commit by default",
            "Lifecycle commands may change Git",
            "git-side-effects",
            "new",
        ),
        (
            "README.md",
            "Alpha",
            "Pre-release",
            "alpha-availability",
            "alpha",
        ),
        (
            "README.md",
            "People remain responsible",
            "Operators review results",
            "evidence-boundaries",
            "People remain responsible",
        ),
    ),
)
def test_pair_specific_contract_findings_are_actionable(
    tmp_path: Path, filename: str, old: str, new: str, code: str, missing: str
) -> None:
    root = _write_contract_project(tmp_path)
    path = root / filename
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == code and item.path == filename)
    assert missing in finding.message


def test_validator_requires_exactly_three_documented_agents(tmp_path: Path) -> None:
    root = _write_contract_project(tmp_path)
    english = root / "README.md"
    english.write_text(ROOT_EN + "| Other Agent | `documented` |\n", encoding="utf-8")

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "documented-agents")
    assert finding.path == "README.md"
    assert "exactly Claude Code, Codex, and OpenCode" in finding.message


def test_validator_reports_unresolved_local_and_fixture_links(tmp_path: Path) -> None:
    root = _write_contract_project(tmp_path)
    guide = root / "docs" / "getting-started.md"
    guide.write_text(
        guide.read_text(encoding="utf-8")
        .replace("../examples/light-complete/brief.md", "../examples/light-complete/missing.md")
        .replace("../examples/light-complete/notes/landscape.md", "missing-guide.md"),
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    assert any(
        item.code == "fixture-reference" and "missing.md" in item.message for item in findings
    )
    assert any(
        item.code == "local-link" and "missing-guide.md" in item.message for item in findings
    )


@pytest.mark.parametrize(
    "claim", ("pip install sdr", "uv tool install sdr", "https://pypi.org/project/sdr/")
)
def test_validator_rejects_package_index_install_claims_while_publication_is_disabled(
    tmp_path: Path, claim: str
) -> None:
    root = _write_contract_project(tmp_path)
    guide = root / "docs" / "getting-started.md"
    guide.write_text(
        guide.read_text(encoding="utf-8") + f"\nInstall with `{claim}`.\n", encoding="utf-8"
    )

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "package-index-install")
    assert finding.path == "docs/getting-started.md"
    assert "publication is disabled" in finding.message


def test_validator_reads_the_declared_coordinate_as_the_only_repository_source(
    tmp_path: Path,
) -> None:
    root = _write_contract_project(tmp_path)

    assert canonical_repository(root) == CANONICAL_REPOSITORY
    assert validate_readme_parity(root) == []


def test_validator_reports_a_missing_or_ambiguous_declared_coordinate(tmp_path: Path) -> None:
    root = _write_contract_project(tmp_path)
    (root / "pyproject.toml").write_text(
        PYPROJECT + f'Homepage = "{CANONICAL_REPOSITORY}-docs"\n', encoding="utf-8"
    )

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "repository-coordinate")
    assert finding.path == "pyproject.toml"
    assert "exactly one" in finding.message


def test_validator_rejects_a_second_repository_identity_source_by_location(tmp_path: Path) -> None:
    root = _write_contract_project(tmp_path)
    module = root / "src" / "sdr" / "constants.py"
    module.parent.mkdir(parents=True)
    module.write_text(f'REPOSITORY = "{CANONICAL_REPOSITORY}"\n', encoding="utf-8")

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "repository-identity-source")
    assert finding.path == "src/sdr/constants.py"
    assert "line 1" in finding.message


def test_validator_reports_a_documented_coordinate_with_file_line_and_value(
    tmp_path: Path,
) -> None:
    root = _write_contract_project(tmp_path)
    guide = root / "docs" / "getting-started.md"
    guide.write_text(
        guide.read_text(encoding="utf-8") + "\nClone https://github.com/other/project.git\n",
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "repository-identity")
    assert finding.path == "docs/getting-started.md"
    assert "line 13" in finding.message
    assert "https://github.com/other/project" in finding.message
    assert CANONICAL_REPOSITORY in finding.message


def test_validator_reports_a_redirect_only_coordinate_without_network_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_contract_project(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nInstall from https://github.com/ign24/sdr\n",
        encoding="utf-8",
    )

    def _forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("repository identity validation must not use the network")

    monkeypatch.setattr(socket, "socket", _forbid_network)
    monkeypatch.setattr(socket, "create_connection", _forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", _forbid_network)

    findings = validate_readme_parity(root)

    finding = next(
        item for item in findings if item.code == "repository-identity" and item.path == "README.md"
    )
    assert "https://github.com/ign24/sdr" in finding.message


@pytest.mark.parametrize(
    "claim",
    (
        "pip install spec-driven-research",
        "uv tool install spec-driven-research",
        "https://pypi.org/project/spec-driven-research/",
    ),
)
def test_validator_rejects_package_index_installation_of_the_distribution_name(
    tmp_path: Path, claim: str
) -> None:
    root = _write_contract_project(tmp_path)
    guide = root / "docs" / "getting-started.md"
    guide.write_text(
        guide.read_text(encoding="utf-8") + f"\nInstall with `{claim}`.\n", encoding="utf-8"
    )

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "package-index-install")
    assert finding.path == "docs/getting-started.md"
    assert "no package-index release exists" in finding.message


@pytest.mark.parametrize(
    "claim",
    (
        "PyPI Trusted Publishing is the intended future release mechanism.",
        "A planned PyPI release will follow.",
        "Una publicacion en PyPI esta prevista para el futuro.",
    ),
)
def test_validator_rejects_documentation_implying_a_planned_package_index_route(
    tmp_path: Path, claim: str
) -> None:
    root = _write_contract_project(tmp_path)
    policy = root / "docs" / "releasing.md"
    policy.write_text(policy.read_text(encoding="utf-8") + f"\n{claim}\n", encoding="utf-8")

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "package-index-install")
    assert finding.path == "docs/releasing.md"
    assert "no package-index release exists" in finding.message


def test_validator_requires_documented_git_installs_to_pin_a_revision(tmp_path: Path) -> None:
    root = _write_contract_project(tmp_path)
    guide = root / "docs" / "getting-started.md"
    guide.write_text(
        guide.read_text(encoding="utf-8")
        + f'\n\n    uv tool install "git+{CANONICAL_REPOSITORY}"\n',
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    finding = next(
        item
        for item in findings
        if item.code == "install-revision" and item.path.endswith("getting-started.md")
    )
    assert "line 14" in finding.message
    assert CANONICAL_REPOSITORY in finding.message


def test_validator_accepts_a_documented_git_install_pinned_to_a_revision(tmp_path: Path) -> None:
    root = _write_contract_project(tmp_path)
    guide = root / "docs" / "getting-started.md"
    guide.write_text(
        guide.read_text(encoding="utf-8")
        + f'\n\n    uv tool install "git+{CANONICAL_REPOSITORY}@${{REVISION}}"\n',
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    assert not [
        item
        for item in findings
        if item.code == "install-revision" and item.path == "docs/getting-started.md"
    ]


def test_validator_reports_missing_contract_with_actionable_message(tmp_path: Path) -> None:
    root = _copy_readmes(tmp_path)
    spanish = root / "README.es.md"
    spanish.write_text(
        spanish.read_text(encoding="utf-8").replace("`sdr verify-probe`", "`probe`"),
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "critical-commands")
    assert finding.path == "README.es.md"
    assert "sdr verify-probe" in finding.message


def test_validator_requires_package_resource_integration_contract(tmp_path: Path) -> None:
    root = _copy_readmes(tmp_path)
    english = root / "README.md"
    english.write_text(
        english.read_text(encoding="utf-8").replace(
            "sdr integrations install --destination", "python -m installer"
        ),
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "integration-installation")
    assert finding.path == "README.md"
    assert "sdr integrations install --destination" in finding.message


def test_validator_requires_prominent_reciprocal_language_links(tmp_path: Path) -> None:
    root = _copy_readmes(tmp_path)
    english = root / "README.md"
    english.write_text(
        english.read_text(encoding="utf-8").replace("[Español](README.es.md)", "Español"),
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    assert any(
        item.code == "language-link" and item.path == "README.md" and "README.es.md" in item.message
        for item in findings
    )


def test_module_cli_is_deterministic_and_machine_usable() -> None:
    first = subprocess.run(
        [sys.executable, "-m", "sdr.readme_parity", str(PROJECT_ROOT)],
        text=True,
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, "-m", "sdr.readme_parity", str(PROJECT_ROOT)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert first.returncode == 0
    assert first.stdout == "README parity: OK\n"
    assert second.stdout == first.stdout


def test_validator_requires_the_pair_to_document_the_same_installation_route(
    tmp_path: Path,
) -> None:
    root = _write_contract_project(tmp_path)
    spanish = root / "README.es.md"
    spanish.write_text(
        spanish.read_text(encoding="utf-8").replace("uv tool install .", "uv sync"),
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "install-route-parity")
    assert finding.path == "README.md"
    assert "checkout-uv" in finding.message
    assert "README.es.md" in finding.message


def test_validator_reports_the_language_that_adds_an_unmatched_installation_route(
    tmp_path: Path,
) -> None:
    root = _write_contract_project(tmp_path)
    english = root / "README.md"
    english.write_text(
        english.read_text(encoding="utf-8") + "\nAlso run `pipx install .` if you prefer.\n",
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    finding = next(item for item in findings if item.code == "install-route-parity")
    assert finding.path == "README.md"
    assert "checkout-pipx" in finding.message


def test_validator_requires_both_languages_to_reference_the_agent_routing_conditions(
    tmp_path: Path,
) -> None:
    root = _write_contract_project(tmp_path)
    spanish = root / "README.es.md"
    spanish.write_text(
        spanish.read_text(encoding="utf-8").replace("cuándo no", "en otros casos"),
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    finding = next(
        item for item in findings if item.code == "agent-routing" and item.path == "README.es.md"
    )
    assert "cuándo no" in finding.message


def test_validator_rejects_a_one_sided_routing_reference_in_either_language(
    tmp_path: Path,
) -> None:
    root = _write_contract_project(tmp_path)
    english = root / "README.md"
    english.write_text(
        english.read_text(encoding="utf-8").replace("when it must not", "and nothing else"),
        encoding="utf-8",
    )

    findings = validate_readme_parity(root)

    finding = next(
        item for item in findings if item.code == "agent-routing" and item.path == "README.md"
    )
    assert "when it must not" in finding.message
