"""Validate and install the public SDR Agent Skills integrations."""

from __future__ import annotations

import argparse
import importlib.resources
import json
import re
import shutil
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from sdr import __version__
from sdr.skill_validation import ACTIVE_COMMANDS, SKILLS

ADAPTERS = (
    "claude-code",
    "opencode",
    "codex",
)
VALID_STATUSES = frozenset({"documented", "verified", "experimental"})

ROUTING_BLOCK_RESOURCE = "agent_routing.md"
ROUTING_BLOCK_TITLE = "# Spec-Driven Research routing"
ROUTING_SELECTING_HEADING = "## Use SDR when"
ROUTING_EXCLUDING_HEADING = "## Do not use SDR when"
ROUTING_GUIDANCE_MARKER = "guidance the user installs"

_ADAPTER_NAMES = {
    "Claude Code": "claude-code",
    "Codex": "codex",
    "OpenCode": "opencode",
}
_PACKAGED_ADAPTERS = {
    f"integrations/{agent}/adapter.yaml": (f"sdr/resources/integrations/{agent}/adapter.yaml")
    for agent in ADAPTERS
}
_PUBLIC_STATUS_SECTIONS = {
    "README.md": "Agent integrations",
    "README.es.md": "Integraciones con agentes",
    "docs/integrations.md": "Status",
}
_CANARY_EVIDENCE_LINK = "[sanitized canary evidence](../integrations/canary-evidence.json)"
_CANARY_TOP_LEVEL_FIELDS = frozenset(
    {"schema_version", "recorded_on", "artifact", "environment", "adapters"}
)
_CANARY_ENVIRONMENT_FIELDS = frozenset(
    {"isolation", "home", "workspace", "python_version", "os", "architecture"}
)
_CANARY_ADAPTER_FIELDS = frozenset(
    {"host_version", "policy", "discovery", "lifecycle", "limitations"}
)
_CANARY_POLICY = {
    "authentication": "no-credentials",
    "model": "not-invoked",
}

_PRIVATE_PATH = re.compile(r"(?<![\w.])/(?:home|Users|root)/[^\s`)]+")
_COMMAND = re.compile(r"\bsdr[ \t]+([a-z][a-z-]*)\b")
_FIXED_MODEL = re.compile(
    r"\b(?:gpt-[0-9][\w.-]*|claude-(?:opus|sonnet|haiku)[\w.-]*|gemini-[0-9][\w.-]*)\b",
    re.IGNORECASE,
)
_DIVERGENT_LIFECYCLE = re.compile(
    r"\b(?:intake|explore|probe|transfer|reuse)\s*(?:->|→)", re.IGNORECASE
)
_FORBIDDEN_FILES = frozenset(
    {
        "package.json",
        "requirements.txt",
        "pyproject.toml",
    }
)
_REQUIRED_METADATA = frozenset(
    {
        "schema_version",
        "agent",
        "status",
        "adapter",
        "format",
        "canonical_skills",
        "skills",
        "discovery",
        "installation",
        "runtime_dependencies",
        "official_docs",
        "status_evidence",
    }
)
_HOST_E2E_FIELDS = frozenset(
    {
        "host_version",
        "sdr_version",
        "acquisition_identity",
        "environment",
        "discovery",
        "lifecycle",
    }
)


@dataclass(frozen=True, order=True)
class IntegrationFinding:
    """One stable integration contract violation."""

    agent: str
    code: str
    detail: str


def install_canonical_skills(destination: Path) -> list[Path]:
    """Copy every packaged canonical skill into an explicit discovery directory."""
    skills_root = importlib.resources.files("sdr").joinpath("resources", "skills")
    packaged: list[tuple[str, bytes]] = []
    for name in SKILLS:
        source = skills_root.joinpath(name, "SKILL.md")
        if not source.is_file():
            raise FileNotFoundError(f"canonical skill is missing: {name}")
        packaged.append((name, source.read_bytes()))

    destination = Path(destination)
    conflicts = [
        destination / name
        for name in SKILLS
        if (destination / name).exists() or (destination / name).is_symlink()
    ]
    if conflicts:
        names = ", ".join(path.name for path in conflicts)
        raise FileExistsError(f"refusing to overwrite existing skills: {names}")

    destination_existed = destination.is_dir()
    destination.mkdir(parents=True, exist_ok=True)
    installed: list[Path] = []
    try:
        for name, content in packaged:
            target = destination / name
            target.mkdir()
            installed.append(target)
            (target / "SKILL.md").write_bytes(content)
    except OSError:
        for target in installed:
            shutil.rmtree(target)
        if not destination_existed:
            try:
                destination.rmdir()
            except OSError:
                pass
        raise
    return installed


def canonical_routing_block() -> str:
    """Return the canonical agent routing block shipped as a package resource."""
    resource = importlib.resources.files("sdr").joinpath(ROUTING_BLOCK_RESOURCE)
    if not resource.is_file():
        raise FileNotFoundError(f"canonical routing block is missing: {ROUTING_BLOCK_RESOURCE}")
    return resource.read_text(encoding="utf-8").strip()


def validate_routing_block(block: str) -> list[IntegrationFinding]:
    """Validate that a routing block is two-sided and states its own limits."""
    findings: list[IntegrationFinding] = []
    text = block.strip()
    if not text.startswith(ROUTING_BLOCK_TITLE):
        findings.append(
            IntegrationFinding("routing", "invalid-routing-block", "routing block title is missing")
        )
    selecting = _routing_conditions(text, ROUTING_SELECTING_HEADING)
    excluding = _routing_conditions(text, ROUTING_EXCLUDING_HEADING)
    if not selecting or not excluding:
        findings.append(
            IntegrationFinding(
                "routing",
                "one-sided-routing-block",
                "routing block must state selecting and excluding conditions",
            )
        )
    normalized = " ".join(text.split())
    if ROUTING_GUIDANCE_MARKER not in normalized or "not enforcement" not in normalized:
        findings.append(
            IntegrationFinding(
                "routing",
                "unenforceable-routing-block",
                "routing block must state that it is user-installed guidance, not enforcement",
            )
        )
    findings.extend(_validate_text("routing", text))
    return findings


def _routing_conditions(text: str, heading: str) -> list[str]:
    if heading not in text:
        return []
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    return [line.strip() for line in section.splitlines() if line.strip().startswith("- ")]


def _validate_canonical_routing_block() -> list[IntegrationFinding]:
    try:
        canonical = canonical_routing_block()
    except FileNotFoundError:
        return [
            IntegrationFinding(
                "routing", "missing-routing-block", "canonical routing block resource is missing"
            )
        ]
    return validate_routing_block(canonical)


def _validate_published_routing_blocks(agent: str, text: str) -> list[IntegrationFinding]:
    try:
        canonical = canonical_routing_block()
    except FileNotFoundError:
        return [
            IntegrationFinding(
                "routing", "missing-routing-block", "canonical routing block resource is missing"
            )
        ]
    occurrences = text.count(ROUTING_BLOCK_TITLE)
    if occurrences == 0:
        return [
            IntegrationFinding(
                agent, "missing-routing-block", "the canonical routing block is not published"
            )
        ]
    if occurrences > 1:
        return [
            IntegrationFinding(
                agent, "duplicate-routing-block", "the routing block is published more than once"
            )
        ]
    if _published_routing_block(text) != canonical:
        return [
            IntegrationFinding(
                agent,
                "divergent-routing-block",
                "published block differs from the canonical source",
            )
        ]
    return []


def _published_routing_block(text: str) -> str:
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(ROUTING_BLOCK_TITLE))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("```"):
            end = index
            break
    return "\n".join(lines[start:end]).strip()


def validate_integrations(project_root: Path) -> list[IntegrationFinding]:
    """Validate all public integration adapters without invoking agent binaries."""
    root = Path(project_root)
    integrations = root / "integrations"
    findings: list[IntegrationFinding] = []
    present = (
        {path.name for path in integrations.iterdir() if path.is_dir()}
        if integrations.is_dir()
        else set()
    )

    for agent in sorted(set(ADAPTERS) - present):
        findings.append(
            IntegrationFinding(agent, "missing-adapter", "adapter directory is missing")
        )
    for agent in sorted(present - set(ADAPTERS)):
        findings.append(
            IntegrationFinding(agent, "unexpected-adapter", "unknown adapter directory")
        )

    findings.extend(_validate_packaged_adapters(root))

    statuses: dict[str, object] = {}
    for agent in ADAPTERS:
        adapter_dir = integrations / agent
        readme = adapter_dir / "README.md"
        metadata_path = adapter_dir / "adapter.yaml"
        if not readme.is_file():
            findings.append(IntegrationFinding(agent, "missing-readme", "README.md is missing"))
        if not metadata_path.is_file():
            findings.append(
                IntegrationFinding(agent, "missing-metadata", "adapter.yaml is missing")
            )
            continue

        try:
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            metadata = None
        if not isinstance(metadata, dict):
            findings.append(
                IntegrationFinding(agent, "invalid-metadata", "adapter.yaml must be a mapping")
            )
            continue

        missing_keys = sorted(_REQUIRED_METADATA - set(metadata))
        if missing_keys:
            findings.append(
                IntegrationFinding(
                    agent, "metadata-keys", "missing keys: " + ", ".join(missing_keys)
                )
            )
        if metadata.get("agent") != agent:
            findings.append(
                IntegrationFinding(agent, "agent-name", "agent must match directory name")
            )
        if metadata.get("status") not in VALID_STATUSES:
            findings.append(
                IntegrationFinding(agent, "invalid-status", "unsupported integration status")
            )
        statuses[agent] = metadata.get("status")
        findings.extend(_validate_status_evidence(agent, metadata))
        if metadata.get("canonical_skills") != "../../skills" or metadata.get("skills") != list(
            SKILLS
        ):
            findings.append(
                IntegrationFinding(agent, "canonical-skills", "must reference all canonical skills")
            )
        if metadata.get("runtime_dependencies") != []:
            findings.append(
                IntegrationFinding(
                    agent, "runtime-dependency", "runtime dependencies are forbidden"
                )
            )
        official_docs = metadata.get("official_docs")
        if not isinstance(official_docs, str) or not official_docs.startswith("https://"):
            findings.append(
                IntegrationFinding(agent, "official-docs", "an HTTPS official source is required")
            )

        files = [path for path in adapter_dir.rglob("*") if path.is_file()]
        for path in files:
            if path.name in _FORBIDDEN_FILES or "hooks" in path.parts or path.name == "SKILL.md":
                findings.append(
                    IntegrationFinding(
                        agent, "forbidden-artifact", f"forbidden adapter file: {path.name}"
                    )
                )
            text = path.read_text(encoding="utf-8")
            findings.extend(_validate_text(agent, text))

        if readme.is_file():
            findings.extend(
                _validate_published_routing_blocks(agent, readme.read_text(encoding="utf-8"))
            )

    findings.extend(_validate_canonical_routing_block())
    findings.extend(_validate_canary_evidence(root))
    findings.extend(_validate_public_statuses(root, statuses))
    return sorted(set(findings))


def _validate_packaged_adapters(root: Path) -> list[IntegrationFinding]:
    findings: list[IntegrationFinding] = []
    config_path = root / "pyproject.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        force_include = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    except (FileNotFoundError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return [
            IntegrationFinding(
                "packaging", "invalid-packaging-config", "Hatch force-include mapping is missing"
            )
        ]
    if not isinstance(force_include, dict):
        return [
            IntegrationFinding(
                "packaging", "invalid-packaging-config", "Hatch force-include must be a mapping"
            )
        ]

    integration_mappings = {
        source: target
        for source, target in force_include.items()
        if isinstance(source, str)
        and isinstance(target, str)
        and target.startswith("sdr/resources/integrations/")
    }
    for source, target in _PACKAGED_ADAPTERS.items():
        agent = Path(source).parent.name
        actual_target = integration_mappings.get(source)
        if actual_target is None:
            findings.append(
                IntegrationFinding(agent, "missing-packaged-adapter", "Hatch mapping is missing")
            )
        elif actual_target != target:
            findings.append(
                IntegrationFinding(
                    agent,
                    "packaged-adapter-target",
                    f"Hatch target must be {target}",
                )
            )
        if not (root / source).is_file():
            findings.append(
                IntegrationFinding(
                    agent, "packaged-adapter-source", "mapped source descriptor is missing"
                )
            )

    for source, target in sorted(integration_mappings.items() - _PACKAGED_ADAPTERS.items()):
        target_parts = Path(target).parts
        agent = target_parts[-2] if len(target_parts) >= 2 else source
        findings.append(
            IntegrationFinding(
                agent,
                "unexpected-packaged-adapter",
                "unsupported Hatch integration resource mapping",
            )
        )
    return findings


def _validate_public_statuses(root: Path, statuses: dict[str, object]) -> list[IntegrationFinding]:
    findings: list[IntegrationFinding] = []
    expected_names = set(_ADAPTER_NAMES)
    for relative_path, section_name in _PUBLIC_STATUS_SECTIONS.items():
        path = root / relative_path
        try:
            rows = _read_status_table(path.read_text(encoding="utf-8"), section_name)
        except (FileNotFoundError, ValueError):
            findings.append(
                IntegrationFinding(
                    "public",
                    "missing-public-status-table",
                    f"integration status table is missing from {relative_path}",
                )
            )
            continue

        names = [row[0] for row in rows]
        for name in sorted(expected_names - set(names)):
            findings.append(
                IntegrationFinding(
                    _ADAPTER_NAMES[name],
                    "missing-public-adapter",
                    f"adapter is missing from {relative_path}",
                )
            )
        for name in sorted(set(names) - expected_names):
            agent = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "public"
            findings.append(
                IntegrationFinding(
                    agent,
                    "unexpected-public-adapter",
                    f"unsupported status row in {relative_path}",
                )
            )
        for name in sorted(expected_names & set(names)):
            matching_rows = [row for row in rows if row[0] == name]
            agent = _ADAPTER_NAMES[name]
            if len(matching_rows) != 1:
                findings.append(
                    IntegrationFinding(
                        agent,
                        "duplicate-public-adapter",
                        f"duplicate status row in {relative_path}",
                    )
                )
                continue
            public_status = matching_rows[0][1]
            if public_status != statuses.get(agent):
                findings.append(
                    IntegrationFinding(
                        agent,
                        "public-status",
                        f"status in {relative_path} must match adapter.yaml",
                    )
                )
    return findings


def _validate_canary_evidence(root: Path) -> list[IntegrationFinding]:
    findings: list[IntegrationFinding] = []
    evidence_path = root / "integrations" / "canary-evidence.json"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [
            IntegrationFinding(
                "public", "missing-canary-evidence", "integrations/canary-evidence.json is missing"
            )
        ]
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [
            IntegrationFinding(
                "public", "invalid-canary-evidence", "canary evidence must be valid UTF-8 JSON"
            )
        ]

    if not isinstance(evidence, dict):
        return [
            IntegrationFinding(
                "public", "invalid-canary-evidence", "canary evidence must be a JSON object"
            )
        ]

    if set(evidence) != _CANARY_TOP_LEVEL_FIELDS or evidence.get("schema_version") != 1:
        findings.append(
            IntegrationFinding(
                "public",
                "invalid-canary-evidence",
                "canary evidence has invalid top-level fields or schema version",
            )
        )

    recorded_on = evidence.get("recorded_on")
    try:
        if (
            not isinstance(recorded_on, str)
            or date.fromisoformat(recorded_on).isoformat() != recorded_on
        ):
            raise ValueError
    except ValueError:
        findings.append(
            IntegrationFinding(
                "public", "invalid-canary-evidence", "recorded_on must be an ISO calendar date"
            )
        )

    artifact = evidence.get("artifact")
    expected_filename = f"spec_driven_research-{__version__}-py3-none-any.whl"
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"package_version", "wheel_filename", "sha256"}
        or artifact.get("package_version") != __version__
        or artifact.get("wheel_filename") != expected_filename
        or not isinstance(artifact.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None
    ):
        findings.append(
            IntegrationFinding(
                "public",
                "invalid-canary-evidence",
                "artifact identity must contain the current exact wheel filename and SHA-256",
            )
        )

    environment = evidence.get("environment")
    if (
        not isinstance(environment, dict)
        or set(environment) != _CANARY_ENVIRONMENT_FIELDS
        or environment.get("isolation") != "env-i"
        or environment.get("home") != "temporary"
        or environment.get("workspace") != "temporary-outside-checkout"
        or not all(
            isinstance(environment.get(field), str) and environment[field].strip()
            for field in _CANARY_ENVIRONMENT_FIELDS
        )
    ):
        findings.append(
            IntegrationFinding(
                "public",
                "invalid-canary-evidence",
                "environment must contain generic isolated host facts",
            )
        )

    serialized = json.dumps(evidence, sort_keys=True)
    if _PRIVATE_PATH.search(serialized) or re.search(
        r"(?:api[_-]?key|password|secret|token)\s*[:=]", serialized, re.IGNORECASE
    ):
        findings.append(
            IntegrationFinding(
                "public",
                "unsafe-canary-evidence",
                "canary evidence contains a private path or credential-like material",
            )
        )

    adapters = evidence.get("adapters")
    if not isinstance(adapters, dict):
        findings.append(
            IntegrationFinding(
                "public", "invalid-canary-evidence", "adapters must be a JSON object"
            )
        )
        adapters = {}
    for agent in sorted(set(ADAPTERS) - set(adapters)):
        findings.append(
            IntegrationFinding(agent, "missing-canary-adapter", "canary result is missing")
        )
    for agent in sorted(set(adapters) - set(ADAPTERS)):
        findings.append(
            IntegrationFinding(agent, "unexpected-canary-adapter", "unsupported canary result")
        )

    for agent in ADAPTERS:
        result = adapters.get(agent)
        if not isinstance(result, dict) or set(result) != _CANARY_ADAPTER_FIELDS:
            findings.append(
                IntegrationFinding(
                    agent, "invalid-canary-evidence", "canary result fields are incomplete"
                )
            )
            continue
        policy = result.get("policy")
        discovery = result.get("discovery")
        lifecycle = result.get("lifecycle")
        limitations = result.get("limitations")
        authentication = policy.get("authentication") if isinstance(policy, dict) else None
        model = policy.get("model") if isinstance(policy, dict) else None
        discovery_result = discovery.get("result") if isinstance(discovery, dict) else None
        lifecycle_result = lifecycle.get("result") if isinstance(lifecycle, dict) else None
        if (
            not isinstance(result.get("host_version"), str)
            or not result["host_version"].strip()
            or not isinstance(policy, dict)
            or set(policy) != {"authentication", "model", "network"}
            or any(policy.get(key) != value for key, value in _CANARY_POLICY.items())
            or policy.get("network") not in {"disabled", "not-used"}
            or not isinstance(discovery, dict)
            or set(discovery) != {"result"}
            or discovery.get("result") not in {"filesystem-only", "discovered", "not-discovered"}
            or not isinstance(lifecycle, dict)
            or set(lifecycle) != {"result"}
            or not isinstance(limitations, list)
            or not limitations
            or not all(isinstance(item, str) and item.strip() for item in limitations)
        ):
            findings.append(
                IntegrationFinding(
                    agent, "invalid-canary-evidence", "canary result is missing required facts"
                )
            )
        if (
            (agent == "claude-code" and discovery_result != "filesystem-only")
            or lifecycle_result != "not-run"
            or authentication != "no-credentials"
            or model != "not-invoked"
        ):
            findings.append(
                IntegrationFinding(
                    agent,
                    "overstated-canary-evidence",
                    "canary evidence exceeds documented no-credential, no-model discovery scope",
                )
            )

    guide_path = root / "docs" / "integrations.md"
    try:
        guide = guide_path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        guide = ""
    if _CANARY_EVIDENCE_LINK not in guide:
        findings.append(
            IntegrationFinding(
                "public",
                "missing-canary-evidence-link",
                "docs/integrations.md must link the sanitized canary evidence",
            )
        )
    if isinstance(artifact, dict) and isinstance(artifact.get("sha256"), str):
        section = guide.split("## Isolated discovery canaries", 1)[-1].split("\n## ", 1)[0]
        claimed_digests = set(re.findall(r"\b[0-9a-f]{64}\b", section))
        if claimed_digests and claimed_digests != {artifact["sha256"]}:
            findings.append(
                IntegrationFinding(
                    "public",
                    "canary-evidence-mismatch",
                    "documented canary digest does not match the evidence record",
                )
            )
    return findings


def _read_status_table(text: str, section_name: str) -> list[tuple[str, str]]:
    marker = f"## {section_name}\n"
    if marker not in text:
        raise ValueError("section missing")
    section = text.split(marker, 1)[1].split("\n## ", 1)[0]
    lines = section.splitlines()
    for index, line in enumerate(lines):
        if not re.fullmatch(r"\|(?:\s*:?-+:?\s*\|)+", line):
            continue
        header = [cell.strip().casefold() for cell in lines[index - 1].strip("|").split("|")]
        status_indexes = [
            position
            for position, value in enumerate(header)
            if "status" in value or "estado" in value
        ]
        if len(status_indexes) != 1:
            raise ValueError("status column missing")
        rows: list[tuple[str, str]] = []
        for row in lines[index + 1 :]:
            if not row.startswith("|"):
                break
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            if len(cells) != len(header):
                raise ValueError("invalid status row")
            rows.append((cells[0], cells[status_indexes[0]].strip("`")))
        return rows
    raise ValueError("table missing")


def _validate_status_evidence(
    agent: str, metadata: dict[object, object]
) -> list[IntegrationFinding]:
    status = metadata.get("status")
    evidence = metadata.get("status_evidence")
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        return [
            IntegrationFinding(agent, "status-evidence", "status evidence schema_version must be 1")
        ]

    host_e2e = evidence.get("host_e2e")
    if status != "verified":
        if host_e2e is not None:
            return [
                IntegrationFinding(
                    agent,
                    "status-evidence",
                    f"{status} status must not claim host E2E evidence",
                )
            ]
        return []

    if not isinstance(host_e2e, dict):
        return [
            IntegrationFinding(
                agent, "status-evidence", "verified status requires complete host E2E evidence"
            )
        ]
    missing = sorted(_HOST_E2E_FIELDS - set(host_e2e))
    if missing:
        return [
            IntegrationFinding(
                agent,
                "status-evidence",
                "verified host E2E evidence is missing: " + ", ".join(missing),
            )
        ]
    if (
        not all(
            isinstance(host_e2e[field], str) and host_e2e[field].strip()
            for field in _HOST_E2E_FIELDS
        )
        or host_e2e["sdr_version"] != __version__
        or host_e2e["discovery"] != "passed"
        or host_e2e["lifecycle"] != "passed"
    ):
        return [
            IntegrationFinding(
                agent,
                "status-evidence",
                "verified host E2E evidence must be complete, passing, and version matched",
            )
        ]
    return []


def _validate_text(agent: str, text: str) -> list[IntegrationFinding]:
    findings: list[IntegrationFinding] = []
    if re.search(r"\bSpecLab\b", text, re.IGNORECASE):
        findings.append(
            IntegrationFinding(agent, "legacy-concept", "legacy adapter concept is forbidden")
        )
    if _PRIVATE_PATH.search(text):
        findings.append(
            IntegrationFinding(agent, "private-path", "private absolute path is forbidden")
        )
    if _FIXED_MODEL.search(text):
        findings.append(IntegrationFinding(agent, "fixed-model", "fixed model names are forbidden"))
    if _DIVERGENT_LIFECYCLE.search(text):
        findings.append(
            IntegrationFinding(agent, "divergent-lifecycle", "lifecycle copies are forbidden")
        )
    for command in sorted(set(_COMMAND.findall(text)) - ACTIVE_COMMANDS):
        findings.append(
            IntegrationFinding(agent, "unknown-command", f"sdr {command} is not active")
        )
    return findings


def render_findings(findings: Sequence[IntegrationFinding]) -> str:
    """Render deterministic findings without environment-specific paths."""
    return "\n".join(f"{item.agent}: {item.code}: {item.detail}" for item in findings)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate adapters or install canonical skills into an explicit scope."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("project_root", type=Path)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "validate":
        findings = validate_integrations(args.project_root)
        if findings:
            print(render_findings(findings))
            return 1
        print("integrations: OK")
        return 0

    try:
        installed = install_canonical_skills(args.destination)
    except (FileExistsError, FileNotFoundError) as error:
        parser.error(str(error))
    print(f"installed {len(installed)} canonical skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
