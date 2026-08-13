import hashlib
import json
import subprocess
import sys
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath

import pytest

from sdr.artifact_audit import ArtifactFinding, audit_artifact, audit_artifacts, render_findings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_RESOURCE_NAMES = {
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
ADAPTER_RESOURCE_NAMES = {
    f"sdr/resources/integrations/{name}/adapter.yaml"
    for name in ("claude-code", "codex", "opencode")
}
INTEGRATION_RESOURCE_NAMES = SKILL_RESOURCE_NAMES | ADAPTER_RESOURCE_NAMES


def _sdist_resource_name(wheel_name: str) -> str:
    return wheel_name.removeprefix("sdr/resources/")


def _write_artifact_pair(
    path: Path,
    mismatched_resource: str | None = None,
    *,
    evidence: bytes | None = None,
    include_evidence: bool = True,
) -> tuple[Path, Path]:
    wheel = path / "spec_driven_research-1.0-py3-none-any.whl"
    sdist = path / "spec_driven_research-1.0.tar.gz"
    canonical = {
        name: f"canonical bytes for {name}\n".encode()
        for name in sorted(INTEGRATION_RESOURCE_NAMES)
    }

    _write_wheel(wheel, "sdr/extra.py")
    with zipfile.ZipFile(wheel, "a") as archive:
        for name, content in canonical.items():
            archive.writestr(
                name,
                b"different private payload\n" if name == mismatched_resource else content,
            )

    if evidence is None:
        evidence = json.dumps(
            {
                "schema_version": 1,
                "artifact": {
                    "package_version": "1.0",
                    "wheel_filename": wheel.name,
                    "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                },
            }
        ).encode()

    with tarfile.open(sdist, "w:gz") as archive:
        for wheel_name, content in canonical.items():
            name = f"spec_driven_research-1.0/{_sdist_resource_name(wheel_name)}"
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, BytesIO(content))
        if include_evidence:
            info = tarfile.TarInfo("spec_driven_research-1.0/integrations/canary-evidence.json")
            info.size = len(evidence)
            archive.addfile(info, BytesIO(evidence))
    return wheel, sdist


def _write_wheel(
    path: Path, extra_name: str, content: str = "safe", *, include_py_typed: bool = True
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("sdr/__init__.py", "__version__ = '1.0.0'\n")
        archive.writestr("sdr/cli.py", "def main(): pass\n")
        if include_py_typed:
            archive.writestr("sdr/py.typed", "")
        archive.writestr("sdr/templates/brief.md", "# Brief\n")
        archive.writestr("example-1.0.dist-info/METADATA", "Name: example\n")
        archive.writestr("example-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        archive.writestr("example-1.0.dist-info/RECORD", "")
        archive.writestr(extra_name, content)


def test_wheel_allowlist_rejects_unknown_top_level_member(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1.0-py3-none-any.whl"
    _write_wheel(wheel, "private-notes.txt")

    findings = audit_artifact(wheel)

    assert any(
        item.code == "unexpected-member" and item.path == "private-notes.txt" for item in findings
    )


def test_artifact_audit_rejects_traversal_without_extracting_it(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1.0-py3-none-any.whl"
    _write_wheel(wheel, "../escaped.txt")

    findings = audit_artifact(wheel)

    assert any(item.code == "unsafe-member" and item.path == "../escaped.txt" for item in findings)
    assert not (tmp_path / "escaped.txt").exists()


def test_artifact_audit_redacts_sensitive_member_names(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1.0-py3-none-any.whl"
    github_token = "ghp_" + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"
    _write_wheel(wheel, github_token)

    output = render_findings(audit_artifact(wheel))

    assert github_token not in output
    assert (
        f"{wheel.name}:<redacted-value-1> [unexpected-member] sensitive content redacted"
        in output.splitlines()
    )


def test_sdist_rejects_prohibited_material_and_redacts_private_paths(tmp_path: Path) -> None:
    sdist = tmp_path / "example-1.0.tar.gz"
    private_path = "/" + "home/person/private/source.py"
    with tarfile.open(sdist, "w:gz") as archive:
        content = f"source={private_path}\n".encode()
        info = tarfile.TarInfo("example-1.0/research/private.md")
        info.size = len(content)
        archive.addfile(info, BytesIO(content))

    findings = audit_artifact(sdist)
    output = render_findings(findings)

    assert {item.code for item in findings} >= {"prohibited-path", "private-absolute-path"}
    assert private_path not in output


def test_sdist_allows_only_the_public_banner_asset(tmp_path: Path) -> None:
    sdist = tmp_path / "spec_driven_research-1.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name in ("assets/sdr-banner.png", "assets/private.png"):
            content = b"safe image bytes"
            info = tarfile.TarInfo(f"spec_driven_research-1.0/{name}")
            info.size = len(content)
            archive.addfile(info, BytesIO(content))

    findings = audit_artifact(sdist)

    assert not any(
        item.code == "unexpected-member"
        and item.path == "spec_driven_research-1.0/assets/sdr-banner.png"
        for item in findings
    )
    assert any(
        item.code == "unexpected-member"
        and item.path == "spec_driven_research-1.0/assets/private.png"
        for item in findings
    )


@pytest.fixture(scope="module")
def built_artifacts(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    dist = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(dist), str(PROJECT_ROOT)],
        check=True,
    )
    return sorted(dist.iterdir())


def test_built_wheel_and_sdist_pass_the_artifact_contract(built_artifacts: list[Path]) -> None:
    assert {path.suffix for path in built_artifacts} == {".gz", ".whl"}
    assert audit_artifacts(built_artifacts) == []


def test_built_artifacts_exclude_the_evaluation_tree(built_artifacts: list[Path]) -> None:
    members: list[str] = []
    for artifact in built_artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as wheel:
                members.extend(wheel.namelist())
        else:
            with tarfile.open(artifact) as sdist:
                members.extend(sdist.getnames())

    assert members
    assert [name for name in members if "bench" in PurePosixPath(name).parts] == []


def test_artifact_audit_requires_typed_package_marker(tmp_path: Path) -> None:
    markerless = tmp_path / "markerless-1.0-py3-none-any.whl"
    _write_wheel(markerless, "sdr/extra.py", include_py_typed=False)

    assert any(
        finding.code == "missing-member" and finding.path == "sdr/py.typed"
        for finding in audit_artifact(markerless)
    )


def test_artifact_audit_requires_exact_integration_resource_set(tmp_path: Path) -> None:
    wheel = tmp_path / "resources-1.0-py3-none-any.whl"
    _write_wheel(wheel, "sdr/extra.py")
    with zipfile.ZipFile(wheel, "a") as archive:
        for name in sorted(INTEGRATION_RESOURCE_NAMES - {min(SKILL_RESOURCE_NAMES)}):
            archive.writestr(name, "canonical\n")
        archive.writestr("sdr/resources/integrations/opencode/README.md", "not packaged\n")
        archive.writestr("sdr/resources/integrations/hermes/adapter.yaml", "unsupported\n")

    findings = audit_artifact(wheel)

    assert any(
        finding.code == "missing-member" and finding.path == min(SKILL_RESOURCE_NAMES)
        for finding in findings
    )
    assert any(
        finding.code == "unexpected-member"
        and finding.path == "sdr/resources/integrations/opencode/README.md"
        for finding in findings
    )
    assert any(
        finding.code == "unexpected-member"
        and finding.path == "sdr/resources/integrations/hermes/adapter.yaml"
        for finding in findings
    )


@pytest.mark.parametrize("resource_name", sorted(INTEGRATION_RESOURCE_NAMES))
def test_artifact_audit_compares_wheel_resources_with_matching_sdist(
    tmp_path: Path, resource_name: str
) -> None:
    wheel, sdist = _write_artifact_pair(tmp_path, resource_name)

    findings = audit_artifacts([sdist, wheel])
    reverse_findings = audit_artifacts([wheel, sdist])

    mismatch = [item for item in findings if item.code == "resource-content-mismatch"]
    assert mismatch == [ArtifactFinding(wheel.name, "resource-content-mismatch", resource_name)]
    assert reverse_findings == findings
    assert "different private payload" not in render_findings(findings)


def test_paired_artifact_audit_requires_canary_evidence_in_matching_sdist(
    tmp_path: Path,
) -> None:
    wheel, sdist = _write_artifact_pair(tmp_path, include_evidence=False)

    findings = audit_artifacts([wheel, sdist])

    assert (
        ArtifactFinding(
            wheel.name,
            "missing-canary-evidence",
            "integrations/canary-evidence.json",
        )
        in findings
    )
    assert not any(finding.code == "missing-canary-evidence" for finding in audit_artifact(wheel))
    assert not any(finding.code == "missing-canary-evidence" for finding in audit_artifact(sdist))


@pytest.mark.parametrize(
    "evidence",
    [
        b"not JSON",
        b"[]",
        b'{"schema_version": 1, "artifact": null}',
        b'{"schema_version": 1, "artifact": {"package_version": "1.0"}}',
        b'{"schema_version": 1, "artifact": {"package_version": "1.0", '
        b'"wheel_filename": "spec_driven_research-1.0-py3-none-any.whl", '
        b'"sha256": "not-a-digest"}}',
    ],
)
def test_paired_artifact_audit_rejects_malformed_canary_evidence(
    tmp_path: Path, evidence: bytes
) -> None:
    wheel, sdist = _write_artifact_pair(tmp_path, evidence=evidence)

    findings = audit_artifacts([wheel, sdist])

    assert (
        ArtifactFinding(
            wheel.name,
            "invalid-canary-evidence",
            "integrations/canary-evidence.json",
        )
        in findings
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("package_version", "1.1"),
        ("wheel_filename", "spec_driven_research-1.0-py3-none-linux_x86_64.whl"),
    ],
)
def test_paired_artifact_audit_rejects_mismatched_canary_wheel_identity(
    tmp_path: Path, field: str, value: str
) -> None:
    wheel, _ = _write_artifact_pair(tmp_path)
    artifact = {
        "package_version": "1.0",
        "wheel_filename": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }
    artifact[field] = value
    evidence = json.dumps({"schema_version": 1, "artifact": artifact}).encode()
    wheel, sdist = _write_artifact_pair(tmp_path, evidence=evidence)

    findings = audit_artifacts([sdist, wheel])

    assert (
        ArtifactFinding(
            wheel.name,
            "canary-artifact-mismatch",
            "integrations/canary-evidence.json",
        )
        in findings
    )


def _stale_digest_evidence() -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "artifact": {
                "package_version": "1.0",
                "wheel_filename": "spec_driven_research-1.0-py3-none-any.whl",
                "sha256": "0" * 64,
            },
        }
    ).encode()


def test_release_artifact_audit_rejects_stale_canary_digest_deterministically(
    tmp_path: Path,
) -> None:
    wheel, sdist = _write_artifact_pair(tmp_path, evidence=_stale_digest_evidence())

    findings = audit_artifacts([wheel, sdist], release=True)
    reverse_findings = audit_artifacts([sdist, wheel], release=True)

    assert (
        ArtifactFinding(
            wheel.name,
            "stale-canary-evidence",
            "integrations/canary-evidence.json",
        )
        in findings
    )
    assert reverse_findings == findings


def test_routine_artifact_audit_ignores_a_canary_digest_from_another_build(
    tmp_path: Path,
) -> None:
    """A development rebuild changes the wheel digest without invalidating the canary."""
    wheel, sdist = _write_artifact_pair(tmp_path, evidence=_stale_digest_evidence())

    findings = audit_artifacts([wheel, sdist])

    assert not any(finding.code == "stale-canary-evidence" for finding in findings)


def test_routine_artifact_audit_still_rejects_a_canary_for_another_version(
    tmp_path: Path,
) -> None:
    """Version and filename binding survives; only the digest match moves to release."""
    evidence = json.dumps(
        {
            "schema_version": 1,
            "artifact": {
                "package_version": "9.9",
                "wheel_filename": "spec_driven_research-1.0-py3-none-any.whl",
                "sha256": "0" * 64,
            },
        }
    ).encode()
    wheel, sdist = _write_artifact_pair(tmp_path, evidence=evidence)

    findings = audit_artifacts([wheel, sdist])

    assert (
        ArtifactFinding(
            wheel.name,
            "canary-artifact-mismatch",
            "integrations/canary-evidence.json",
        )
        in findings
    )


def test_module_cli_enforces_the_canary_digest_only_with_release(tmp_path: Path) -> None:
    wheel, sdist = _write_artifact_pair(tmp_path, evidence=_stale_digest_evidence())
    command = [sys.executable, "-m", "sdr.artifact_audit", str(wheel), str(sdist)]

    routine = subprocess.run(command, capture_output=True, text=True, check=False)
    release = subprocess.run([*command, "--release"], capture_output=True, text=True, check=False)

    assert "stale-canary-evidence" not in routine.stdout
    assert release.returncode == 1
    assert "stale-canary-evidence" in release.stdout


def test_module_cli_audits_all_artifacts_without_sensitive_output(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1.0-py3-none-any.whl"
    private_path = "/" + "Users/person/private/file"
    _write_wheel(wheel, "sdr/private.txt", private_path)

    result = subprocess.run(
        [sys.executable, "-m", "sdr.artifact_audit", str(wheel)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "private-absolute-path" in result.stdout
    assert private_path not in result.stdout
