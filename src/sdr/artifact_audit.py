"""Audit wheel and source-distribution contents against the public package contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .public_tree_audit import Finding as TreeFinding
from .public_tree_audit import RedactionContext, audit_bytes, audit_tree, redact_sensitive

MAX_MEMBER_SIZE = 25 * 1024 * 1024
MAX_TOTAL_SIZE = 100 * 1024 * 1024
WHEEL_METADATA_RE = re.compile(r"^[A-Za-z0-9_.]+-[^/]+\.dist-info$")
SDIST_ROOT_RE = re.compile(r"^spec_driven_research-[^/]+$")
SDIST_FILES = {
    ".gitignore",
    ".gitleaks.toml",
    "AGENTS.md",
    "assets/sdr-banner.png",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "PKG-INFO",
    "README.es.md",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
}
SDIST_DIRECTORIES = {
    ".github",
    "docs",
    "examples",
    "integrations",
    "openspec/specs",
    "skills",
    "src/sdr",
    "tests",
}
SKILL_RESOURCES = {
    f"sdr/resources/skills/{name}/SKILL.md"
    for name in (
        "sdr-explore",
        "sdr-intake",
        "sdr-new",
        "sdr-probe",
        "sdr-reuse",
        "sdr-status",
        "sdr-transfer",
    )
}
ADAPTER_RESOURCES = {
    f"sdr/resources/integrations/{name}/adapter.yaml"
    for name in ("claude-code", "codex", "opencode")
}
INTEGRATION_RESOURCES = SKILL_RESOURCES | ADAPTER_RESOURCES
SDIST_INTEGRATION_RESOURCES = {
    name.removeprefix("sdr/resources/"): name for name in INTEGRATION_RESOURCES
}
CANARY_EVIDENCE_PATH = "integrations/canary-evidence.json"


@dataclass(frozen=True)
class ArtifactFinding:
    artifact: str
    code: str
    path: str
    line: int | None = None


@dataclass(frozen=True)
class _Member:
    name: str
    size: int
    is_file: bool
    is_safe_type: bool
    source: zipfile.ZipInfo | tarfile.TarInfo


def audit_artifact(path: Path) -> list[ArtifactFinding]:
    """Audit one wheel or gzipped source distribution without unsafe extraction."""
    path = path.resolve()
    try:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                members = [_zip_member(item) for item in archive.infolist()]
                return _audit_members(path, members, lambda item: archive.read(item.source))
        if path.name.endswith(".tar.gz"):
            with tarfile.open(path, "r:gz") as archive:
                members = [_tar_member(item) for item in archive.getmembers()]
                return _audit_members(path, members, lambda item: _read_tar(archive, item))
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return [ArtifactFinding(path.name, "invalid-archive", path.name)]
    return [ArtifactFinding(path.name, "unsupported-artifact", path.name)]


def audit_artifacts(paths: Iterable[Path]) -> list[ArtifactFinding]:
    """Audit artifacts in deterministic path order."""
    ordered_paths = sorted(path.resolve() for path in paths)
    findings = [item for path in ordered_paths for item in audit_artifact(path)]
    findings.extend(_resource_comparison_findings(ordered_paths))
    return sorted(findings, key=lambda item: (item.artifact, item.path, item.line or 0, item.code))


def render_findings(findings: Sequence[ArtifactFinding]) -> str:
    """Render locations and categories without rendering matched content."""
    context = RedactionContext()
    lines = []
    for finding in findings:
        location = redact_sensitive(f"{finding.artifact}:{finding.path}", context=context)
        if finding.line is not None:
            location += f":{finding.line}"
        lines.append(f"{location} [{finding.code}] sensitive content redacted")
    return "\n".join(lines)


def _audit_members(
    artifact: Path,
    members: list[_Member],
    read_member: Callable[[_Member], bytes],
) -> list[ArtifactFinding]:
    findings: list[ArtifactFinding] = []
    seen: set[str] = set()
    total_size = 0
    safe_files: list[_Member] = []
    names = {item.name for item in members if item.is_file}

    for member in members:
        if member.name in seen:
            findings.append(ArtifactFinding(artifact.name, "duplicate-member", member.name))
        seen.add(member.name)
        if not _safe_name(member.name) or not member.is_safe_type:
            findings.append(ArtifactFinding(artifact.name, "unsafe-member", member.name))
            continue
        if not _allowed_member(artifact, member.name):
            findings.append(ArtifactFinding(artifact.name, "unexpected-member", member.name))
        if not member.is_file:
            continue
        total_size += member.size
        if member.size > MAX_MEMBER_SIZE or total_size > MAX_TOTAL_SIZE:
            findings.append(ArtifactFinding(artifact.name, "oversized-member", member.name))
            continue
        safe_files.append(member)
        findings.extend(
            _tree_findings(artifact.name, audit_bytes(read_member(member), member.name))
        )

    findings.extend(_required_findings(artifact, names))
    with tempfile.TemporaryDirectory(prefix="sdr-artifact-audit-") as directory:
        root = Path(directory)
        for member in safe_files:
            target = root.joinpath(*PurePosixPath(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read_member(member))
        findings.extend(_tree_findings(artifact.name, audit_tree(root)))
    return sorted(findings, key=lambda item: (item.path, item.line or 0, item.code))


def _allowed_member(artifact: Path, name: str) -> bool:
    parts = PurePosixPath(name).parts
    if artifact.suffix == ".whl":
        if name.startswith("sdr/resources/"):
            return name in INTEGRATION_RESOURCES
        return bool(parts) and (
            parts[0] == "sdr" or WHEEL_METADATA_RE.fullmatch(parts[0]) is not None
        )
    if not parts or SDIST_ROOT_RE.fullmatch(parts[0]) is None:
        return False
    relative = "/".join(parts[1:])
    return relative in SDIST_FILES or any(
        relative == prefix or relative.startswith(f"{prefix}/") for prefix in SDIST_DIRECTORIES
    )


def _required_findings(artifact: Path, names: set[str]) -> list[ArtifactFinding]:
    if artifact.suffix == ".whl":
        required = {
            "sdr/__init__.py",
            "sdr/cli.py",
            "sdr/py.typed",
            "sdr/templates/brief.md",
            *INTEGRATION_RESOURCES,
        }
        metadata_required = {"METADATA", "WHEEL", "RECORD"}
        missing = required - names
        for metadata_name in metadata_required:
            if not any(
                WHEEL_METADATA_RE.fullmatch(PurePosixPath(name).parts[0])
                and PurePosixPath(name).name == metadata_name
                for name in names
            ):
                missing.add(f"*.dist-info/{metadata_name}")
    else:
        relative_names = {
            "/".join(PurePosixPath(name).parts[1:])
            for name in names
            if len(PurePosixPath(name).parts) > 1
        }
        missing = {
            "LICENSE",
            "README.md",
            "pyproject.toml",
            "src/sdr/__init__.py",
            "src/sdr/cli.py",
            "src/sdr/py.typed",
            "src/sdr/templates/brief.md",
        } - relative_names
    return [ArtifactFinding(artifact.name, "missing-member", name) for name in sorted(missing)]


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _zip_member(info: zipfile.ZipInfo) -> _Member:
    mode = info.external_attr >> 16
    is_symlink = (mode & 0o170000) == 0o120000
    return _Member(info.filename, info.file_size, not info.is_dir(), not is_symlink, info)


def _tar_member(info: tarfile.TarInfo) -> _Member:
    return _Member(info.name, info.size, info.isfile(), info.isfile() or info.isdir(), info)


def _read_tar(archive: tarfile.TarFile, member: _Member) -> bytes:
    extracted = archive.extractfile(member.source)
    if extracted is None:
        return b""
    with extracted:
        return extracted.read(MAX_MEMBER_SIZE + 1)


def _tree_findings(artifact: str, findings: list[TreeFinding]) -> list[ArtifactFinding]:
    return [ArtifactFinding(artifact, item.code, item.path, item.line) for item in findings]


def _resource_comparison_findings(paths: Sequence[Path]) -> list[ArtifactFinding]:
    wheels: dict[tuple[str, str], list[tuple[Path, dict[str, bytes]]]] = {}
    sdists: dict[tuple[str, str], list[tuple[dict[str, bytes], bytes | None]]] = {}
    for path in paths:
        identity = _artifact_identity(path)
        resources = _read_integration_resources(path)
        if identity is None or resources is None:
            continue
        if path.suffix == ".whl":
            wheels.setdefault(identity, []).append((path, resources))
        else:
            sdists.setdefault(identity, []).append((resources, _read_sdist_canary_evidence(path)))

    findings = []
    for identity in sorted(wheels.keys() & sdists.keys()):
        for wheel, wheel_resources in wheels[identity]:
            for sdist_resources, canary_evidence in sdists[identity]:
                for name in sorted(INTEGRATION_RESOURCES):
                    if wheel_resources.get(name) != sdist_resources.get(name):
                        findings.append(
                            ArtifactFinding(wheel.name, "resource-content-mismatch", name)
                        )
                findings.extend(_canary_evidence_findings(wheel, identity[1], canary_evidence))
    return findings


def _canary_evidence_findings(
    wheel: Path, version: str, evidence_bytes: bytes | None
) -> list[ArtifactFinding]:
    def finding(code: str) -> ArtifactFinding:
        return ArtifactFinding(wheel.name, code, CANARY_EVIDENCE_PATH)

    if evidence_bytes is None:
        return [finding("missing-canary-evidence")]
    try:
        evidence = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [finding("invalid-canary-evidence")]

    artifact = evidence.get("artifact") if isinstance(evidence, dict) else None
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema_version") != 1
        or not isinstance(artifact, dict)
        or set(artifact) != {"package_version", "wheel_filename", "sha256"}
        or not isinstance(artifact.get("package_version"), str)
        or not artifact["package_version"]
        or not isinstance(artifact.get("wheel_filename"), str)
        or not artifact["wheel_filename"]
        or not isinstance(artifact.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None
    ):
        return [finding("invalid-canary-evidence")]
    if artifact["package_version"] != version or artifact["wheel_filename"] != wheel.name:
        return [finding("canary-artifact-mismatch")]
    if artifact["sha256"] != _sha256(wheel):
        return [finding("stale-canary-evidence")]
    return []


def _artifact_identity(path: Path) -> tuple[str, str] | None:
    if path.suffix == ".whl":
        parts = path.name.removesuffix(".whl").split("-")
        if len(parts) < 5:
            return None
        distribution, version = parts[:2]
    elif path.name.endswith(".tar.gz"):
        distribution, separator, version = path.name.removesuffix(".tar.gz").rpartition("-")
        if not separator:
            return None
    else:
        return None
    return distribution.replace("-", "_").lower(), version.lower()


def _read_integration_resources(path: Path) -> dict[str, bytes] | None:
    try:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                members = [_zip_member(item) for item in archive.infolist()]
                return _read_selected_resources(
                    members,
                    lambda item: archive.read(item.source),
                    {name: name for name in INTEGRATION_RESOURCES},
                )
        if path.name.endswith(".tar.gz"):
            with tarfile.open(path, "r:gz") as archive:
                members = [_tar_member(item) for item in archive.getmembers()]
                root_names = {
                    PurePosixPath(item.name).parts[0]
                    for item in members
                    if PurePosixPath(item.name).parts
                    and SDIST_ROOT_RE.fullmatch(PurePosixPath(item.name).parts[0])
                }
                if len(root_names) != 1:
                    return {}
                root = next(iter(root_names))
                selected = {
                    f"{root}/{source_name}": wheel_name
                    for source_name, wheel_name in SDIST_INTEGRATION_RESOURCES.items()
                }
                return _read_selected_resources(
                    members, lambda item: _read_tar(archive, item), selected
                )
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return None
    return None


def _read_sdist_canary_evidence(path: Path) -> bytes | None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = [_tar_member(item) for item in archive.getmembers()]
            root_names = {
                PurePosixPath(item.name).parts[0]
                for item in members
                if PurePosixPath(item.name).parts
                and SDIST_ROOT_RE.fullmatch(PurePosixPath(item.name).parts[0])
            }
            if len(root_names) != 1:
                return None
            root = next(iter(root_names))
            selected = _read_selected_resources(
                members,
                lambda item: _read_tar(archive, item),
                {f"{root}/{CANARY_EVIDENCE_PATH}": CANARY_EVIDENCE_PATH},
            )
            return selected.get(CANARY_EVIDENCE_PATH)
    except (OSError, tarfile.TarError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_selected_resources(
    members: Sequence[_Member],
    read_member: Callable[[_Member], bytes],
    selected: dict[str, str],
) -> dict[str, bytes]:
    resources: dict[str, bytes] = {}
    seen: set[str] = set()
    total_size = 0
    for member in members:
        target_name = selected.get(member.name)
        if target_name is None:
            continue
        if target_name in seen:
            resources.pop(target_name, None)
            continue
        seen.add(target_name)
        total_size += member.size
        if (
            not member.is_file
            or not member.is_safe_type
            or not _safe_name(member.name)
            or member.size > MAX_MEMBER_SIZE
            or total_size > MAX_TOTAL_SIZE
        ):
            continue
        resources[target_name] = read_member(member)
    return resources


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(argv)
    findings = audit_artifacts(args.artifacts)
    if findings:
        print(render_findings(findings))
        return 1
    print(f"artifacts: OK ({len(args.artifacts)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
