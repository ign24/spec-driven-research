"""Deterministic semantic parity checks for the English and Spanish READMEs."""

from __future__ import annotations

import argparse
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParityFinding:
    code: str
    path: str
    message: str


CRITICAL_COMMANDS = (
    "sdr new",
    "sdr check",
    "sdr advance",
    "sdr status",
    "sdr snapshot",
    "sdr verify-claims",
    "sdr resolve-claim",
    "sdr verify-probe",
    "sdr approve",
    "sdr reopen",
    "sdr drop",
    "sdr archive",
    "sdr index",
    "sdr doctor",
    "sdr migrate",
    "sdr context",
)
STAGES = ("intake", "explore", "probe", "transfer", "reuse")
INTEGRATIONS = ("Claude Code", "Codex", "OpenCode")
REQUIRED_LINKS = (
    "docs/workflow.md",
    "docs/cli-reference.md",
    "docs/evidence-model.md",
    "docs/validation.md",
    "docs/integrations.md",
    "docs/security-model.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "LICENSE",
)
SHARED_CONTRACTS = {
    "critical-commands": CRITICAL_COMMANDS,
    "five-stages": STAGES,
    "modes": ("full", "light"),
    "integrations": INTEGRATIONS,
    "integration-installation": (
        "sdr integrations install --destination",
        ".claude/skills",
        ".agents/skills",
        ".opencode/skills",
        "SDR_ROOT",
    ),
    "integration-statuses": ("`documented`", "`verified`", "`experimental`"),
    "status": ("sdr status", "--json"),
    "links": REQUIRED_LINKS,
    "compatibility": ("Python 3.12", "uv tool install .", "python -m pip install ."),
    "probe-execution": ("verify.action: run", "argv", "without a shell|sin un shell"),
    "controls": (
        "Structural|Estructural",
        "Evidential",
        "Textual anchoring|Anclaje textual",
        "Executable|Ejecutable",
        "Hash consistency|Consistencia de hashes",
        "HITL",
    ),
    "security": (
        "trust boundaries|límites de confianza",
        "credentials|credenciales",
        "sandbox",
    ),
}
LOCALIZED_CONTRACTS = {
    "README.md": {
        "offline-skipped": ("--offline", "skipped", "not passed"),
        "context-graph": ("Context Graph", "optional", "non-blocking", "not complete lineage"),
    },
    "README.es.md": {
        "offline-skipped": ("--offline", "omitid", "no aprob"),
        "context-graph": ("Context Graph", "opcional", "no bloqueante", "trazabilidad completa"),
    },
}
LANGUAGE_LINKS = {
    "README.md": "README.es.md",
    "README.es.md": "README.md",
    "docs/README.md": "README.es.md",
    "docs/README.es.md": "README.md",
    "docs/getting-started.md": "getting-started.es.md",
    "docs/getting-started.es.md": "getting-started.md",
}
PAIR_CONTRACTS = {
    "README.md": {
        "outcome-chain": (
            "question",
            "evidence",
            "optional probe",
            "human-approved decision",
            "reusable asset",
        ),
        "alpha-availability": (
            "alpha",
            "tagged GitHub releases|GitHub releases",
            "no PyPI release|or PyPI release|does not publish to PyPI",
            "canonical GitHub source|explicit revision",
        ),
        "evidence-boundaries": (
            "does not prove source truth|guarantee that cited material is true",
            "People remain responsible",
        ),
        "git-side-effects": (
            "`new`, `advance`, `reopen`, `drop`, and `archive` commit by default",
            "--no-commit",
        ),
        "agent-routing": (
            "routing block",
            "when a host agent invokes SDR|when to invoke SDR",
            "when it must not",
        ),
    },
    "README.es.md": {
        "outcome-chain": (
            "pregunta",
            "evidencia|pruebas",
            "probe opcional",
            "decisión con aprobación humana|decisión aprobada por una persona",
            "activo reutilizable",
        ),
        "alpha-availability": (
            "alfa|alpha",
            "releases de GitHub|release en GitHub",
            "no tiene release en PyPI|no hay release en PyPI|ni en PyPI|no publica en PyPI",
            "código fuente canónico|revisión explícita",
        ),
        "evidence-boundaries": (
            "no demuestra la verdad de las fuentes|garantía de veracidad",
            "Las personas responden|Las personas siguen siendo responsables",
        ),
        "git-side-effects": (
            "`new`, `advance`, `reopen`, `drop` y `archive` crean commits por defecto",
            "--no-commit",
        ),
        "agent-routing": (
            "bloque de enrutamiento|routing block",
            "cuándo invocar SDR|cuándo un agente anfitrión invoca SDR",
            "cuándo no",
        ),
    },
    "docs/README.md": {
        "documentation-routes": (
            "getting-started.md",
            "workflow.md",
            "cli-reference.md",
            "evidence-model.md",
            "security-model.md",
            "integrations.md",
            "validation.md",
            "releasing.md",
        ),
    },
    "docs/README.es.md": {
        "documentation-routes": (
            "getting-started.es.md",
            "workflow.md",
            "cli-reference.md",
            "evidence-model.md",
            "security-model.md",
            "integrations.md",
            "validation.md",
            "releasing.md",
        ),
    },
    "docs/getting-started.md": {
        "light-lifecycle": ("intake -> explore -> transfer -> reuse -> done",),
        "light-boundaries": ("offline", "no probe", "explicit", "approve", "reuse", "mandatory"),
        "git-side-effects": ("commit by default", "--no-commit", "Git"),
        "synthetic-fixture": (
            "../examples/light-complete/README.md",
            "../examples/light-complete/brief.md",
            "../examples/light-complete/notes/landscape.md",
            "../examples/light-complete/decision-memo.md",
            "../examples/light-complete/assets/checklist.md",
        ),
    },
    "docs/getting-started.es.md": {
        "light-lifecycle": ("intake -> explore -> transfer -> reuse -> done",),
        "light-boundaries": (
            "sin red|offline",
            "no ejecuta probes|sin probe",
            "explícit",
            "approve",
            "reuse",
            "obligatori",
        ),
        "git-side-effects": ("commits por defecto|commit por defecto", "--no-commit", "Git"),
        "synthetic-fixture": (
            "../examples/light-complete/README.md",
            "../examples/light-complete/brief.md",
            "../examples/light-complete/notes/landscape.md",
            "../examples/light-complete/decision-memo.md",
            "../examples/light-complete/assets/checklist.md",
        ),
    },
}
ONBOARDING_DOCUMENTS = tuple(LANGUAGE_LINKS)
DOCUMENTED_AGENTS = frozenset(INTEGRATIONS)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]*]\(([^)\s]+)(?:\s+[^)]*)?\)")
PACKAGE_INDEX_CLAIMS = (
    re.compile(r"\bpip install\s+(?![\"']?\.)sdr\b", re.IGNORECASE),
    re.compile(r"\buv tool install\s+(?![\"']?\.)sdr\b", re.IGNORECASE),
    re.compile(r"https?://pypi\.org/project/sdr(?:/|\b)", re.IGNORECASE),
    re.compile(r"\b(?:install(?:ed)? from|available on) PyPI\b", re.IGNORECASE),
    re.compile(r"\bpip install\s+[\"']?spec-driven-research\b", re.IGNORECASE),
    re.compile(r"\buv tool install\s+[\"']?spec-driven-research\b", re.IGNORECASE),
    re.compile(r"https?://pypi\.org/project/spec-driven-research(?:/|\b)", re.IGNORECASE),
    re.compile(
        r"\b(?:planned|future|intended|upcoming|planeado|futur\w*|previst\w*)\b[^.\n]{0,80}\bPyPI\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bPyPI\b[^.\n]{0,80}\b(?:planned|intended|future release|will be published"
        r"|coming soon|se publicar\w*|planeado|previst\w*)\b",
        re.IGNORECASE,
    ),
)
REPOSITORY_URL = re.compile(r"https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
GIT_INSTALL_URL = re.compile(
    r"git\+(?P<repository>https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)"
    r"(?P<revision>@[^\s\"']+)?"
)
IDENTITY_SOURCE_ROOT = "src/sdr"
INSTALL_ROUTES = {
    "git-revision": re.compile(
        r"git\+https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+@[^\s\"']+"
    ),
    "git-unpinned": re.compile(
        r"git\+https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?![@A-Za-z0-9._/-])"
    ),
    "checkout-uv": re.compile(r"uv tool install\s+[\"']?\.[\"']?"),
    "checkout-pip": re.compile(r"pip install\s+[\"']?\.[\"']?"),
    "checkout-pipx": re.compile(r"pipx install\s+[\"']?\.[\"']?"),
    "contributor-sync": re.compile(r"uv sync\b"),
}
ROUTE_PAIRS = (("README.md", "README.es.md"),)


def validate_readme_parity(root: Path) -> list[ParityFinding]:
    """Validate required concepts without comparing translated prose line by line."""
    findings: list[ParityFinding] = []
    for filename, counterpart in LANGUAGE_LINKS.items():
        path = root / filename
        if not path.is_file():
            code = "missing-readme" if path.parent == root else "missing-document"
            findings.append(ParityFinding(code, filename, f"create {filename}"))
            continue

        text = path.read_text(encoding="utf-8")
        opening = "\n".join(text.splitlines()[:15])
        if f"]({counterpart})" not in opening:
            findings.append(
                ParityFinding(
                    "language-link",
                    filename,
                    f"add a prominent reciprocal link to {counterpart} within the first 15 lines",
                )
            )

        if filename in LOCALIZED_CONTRACTS:
            for code, markers in SHARED_CONTRACTS.items():
                _require_markers(findings, filename, text, code, markers)
            for code, markers in LOCALIZED_CONTRACTS[filename].items():
                _require_markers(findings, filename, text, code, markers)
            _validate_documented_agents(findings, filename, text)
        for code, markers in PAIR_CONTRACTS[filename].items():
            contract_text = "\n".join(text.splitlines()[:30]) if code == "outcome-chain" else text
            _require_markers(findings, filename, contract_text, code, markers)

        _validate_local_links(findings, root, filename, text)

    _reject_package_index_claims(findings, root)
    _validate_repository_identity(findings, root)
    _require_pinned_install_revision(findings, root)
    _require_matching_install_routes(findings, root)

    return sorted(findings, key=lambda item: (item.path, item.code, item.message))


def normalize_repository(url: str) -> str:
    """Reduce a repository URL to its bare canonical coordinate form."""
    return url.rstrip("/").removesuffix(".git")


def declared_repository_coordinates(root: Path) -> list[str]:
    """Return every repository coordinate declared under `[project.urls]`."""
    manifest = root / "pyproject.toml"
    if not manifest.is_file():
        return []
    config = tomllib.loads(manifest.read_text(encoding="utf-8"))
    urls = config.get("project", {}).get("urls", {})
    coordinates = {
        normalize_repository(str(value))
        for value in urls.values()
        if REPOSITORY_URL.fullmatch(normalize_repository(str(value)))
    }
    return sorted(coordinates)


def canonical_repository(root: Path) -> str | None:
    """Return the single declared repository coordinate, or None when it is not unique."""
    coordinates = declared_repository_coordinates(root)
    if len(coordinates) != 1:
        return None
    return coordinates[0]


def public_documents(root: Path) -> list[str]:
    """List the public documentation files subject to repository and install contracts."""
    names = [name for name in ("README.md", "README.es.md") if (root / name).is_file()]
    docs = root / "docs"
    if docs.is_dir():
        names.extend(f"docs/{path.name}" for path in sorted(docs.glob("*.md")))
    return names


def _validate_repository_identity(findings: list[ParityFinding], root: Path) -> None:
    canonical = canonical_repository(root)
    if canonical is None:
        findings.append(
            ParityFinding(
                "repository-coordinate",
                "pyproject.toml",
                "declare exactly one canonical repository coordinate under [project.urls]",
            )
        )
        return

    for filename in public_documents(root):
        text = (root / filename).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            for match in REPOSITORY_URL.finditer(line):
                documented = normalize_repository(match.group(0))
                if documented == canonical:
                    continue
                findings.append(
                    ParityFinding(
                        "repository-identity",
                        filename,
                        f"line {number}: replace documented repository {documented} "
                        f"with the declared coordinate {canonical}; "
                        "a rename redirect is not an accepted resolution",
                    )
                )

    _reject_second_identity_source(findings, root)


def _reject_second_identity_source(findings: list[ParityFinding], root: Path) -> None:
    package = root / IDENTITY_SOURCE_ROOT
    if not package.is_dir():
        return
    for module in sorted(package.rglob("*.py")):
        relative = module.relative_to(root).as_posix()
        for number, line in enumerate(module.read_text(encoding="utf-8").splitlines(), start=1):
            match = REPOSITORY_URL.search(line)
            if not match:
                continue
            findings.append(
                ParityFinding(
                    "repository-identity-source",
                    relative,
                    f"line {number}: remove the repository coordinate {match.group(0)}; "
                    "[project.urls] is the only identity source",
                )
            )


def _require_pinned_install_revision(findings: list[ParityFinding], root: Path) -> None:
    for filename in public_documents(root):
        text = (root / filename).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            for match in GIT_INSTALL_URL.finditer(line):
                if match.group("revision"):
                    continue
                findings.append(
                    ParityFinding(
                        "install-revision",
                        filename,
                        f"line {number}: pin an explicit revision on "
                        f"{match.group('repository')}; unpinned git installs are not supported",
                    )
                )


def _validate_documented_agents(findings: list[ParityFinding], filename: str, text: str) -> None:
    documented = {
        match.group(1).strip()
        for match in re.finditer(r"^\|\s*([^|]+?)\s*\|[^\n]*`documented`", text, re.MULTILINE)
    }
    if documented != DOCUMENTED_AGENTS:
        findings.append(
            ParityFinding(
                "documented-agents",
                filename,
                "list exactly Claude Code, Codex, and OpenCode with `documented` status",
            )
        )


def _validate_local_links(
    findings: list[ParityFinding], root: Path, filename: str, text: str
) -> None:
    source = root / filename
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.strip("<>").split("#", 1)[0]
        if not target or target.startswith(("#", "mailto:")) or "://" in target:
            continue
        resolved = source.parent / target
        if resolved.exists():
            continue
        code = "fixture-reference" if "examples/light-complete" in target else "local-link"
        findings.append(ParityFinding(code, filename, f"fix unresolved local target: {target}"))


def _reject_package_index_claims(findings: list[ParityFinding], root: Path) -> None:
    for filename in public_documents(root):
        text = (root / filename).read_text(encoding="utf-8")
        for pattern in PACKAGE_INDEX_CLAIMS:
            match = pattern.search(text)
            if match:
                message = (
                    f"remove package-index claim {match.group(0)!r}; no package-index "
                    "release exists and publication is disabled"
                )
                findings.append(
                    ParityFinding(
                        "package-index-install",
                        filename,
                        message,
                    )
                )
                break


def documented_install_routes(root: Path, filename: str) -> set[str]:
    """Return the identifiers of the installation routes a document presents."""
    path = root / filename
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    return {route for route, pattern in INSTALL_ROUTES.items() if pattern.search(text)}


def _require_matching_install_routes(findings: list[ParityFinding], root: Path) -> None:
    for english, spanish in ROUTE_PAIRS:
        english_routes = documented_install_routes(root, english)
        spanish_routes = documented_install_routes(root, spanish)
        for filename, counterpart, unmatched in (
            (english, spanish, english_routes - spanish_routes),
            (spanish, english, spanish_routes - english_routes),
        ):
            if not unmatched:
                continue
            findings.append(
                ParityFinding(
                    "install-route-parity",
                    filename,
                    f"{filename} documents installation route(s) "
                    f"{', '.join(sorted(unmatched))} that {counterpart} does not; "
                    "both languages must present the same supported route",
                )
            )


def _require_markers(
    findings: list[ParityFinding],
    filename: str,
    text: str,
    code: str,
    markers: Sequence[str],
) -> None:
    normalized = " ".join(text.casefold().split())
    missing = [
        marker
        for marker in markers
        if not any(option.casefold() in normalized for option in marker.split("|"))
    ]
    if missing:
        findings.append(
            ParityFinding(
                code,
                filename,
                f"document the missing contract markers: {', '.join(missing)}",
            )
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    findings = validate_readme_parity(args.root)
    if findings:
        for finding in findings:
            print(f"{finding.path} [{finding.code}] {finding.message}")
        return 1
    print("README parity: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
