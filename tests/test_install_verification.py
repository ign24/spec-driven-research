"""Installation verification from a pinned repository revision.

The network-dependent verification is marked ``installation`` so an offline run
deselects it with an explicit reason instead of silently omitting it.
"""

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from sdr import install_verification

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _claimed_python_versions() -> tuple[str, ...]:
    """Return every Python minor the distribution claims to support."""
    manifest = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    prefix = "Programming Language :: Python :: "
    return tuple(
        classifier.removeprefix(prefix)
        for classifier in manifest["project"]["classifiers"]
        if classifier.startswith(prefix) and "." in classifier.removeprefix(prefix)
    )


CLAIMED_PYTHON_VERSIONS = _claimed_python_versions()


@pytest.fixture(scope="session", params=CLAIMED_PYTHON_VERSIONS)
def verification(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> install_verification.InstallVerification:
    """Install the revision under test once per claimed Python minor."""
    result = install_verification.verify_installation(
        root=REPOSITORY_ROOT,
        workspace=tmp_path_factory.mktemp(f"install-verification-{request.param}"),
        python_version=request.param,
    )
    if not result.performed:
        pytest.skip(f"installation verification unavailable: {result.skip_reason}")
    return result


def test_publication_credential_variables_cover_the_known_publishers() -> None:
    assert {
        "TWINE_USERNAME",
        "TWINE_PASSWORD",
        "TWINE_REPOSITORY_URL",
        "UV_PUBLISH_TOKEN",
        "PYPI_API_TOKEN",
        "HATCH_INDEX_AUTH",
        "FLIT_PASSWORD",
    } <= install_verification.PUBLICATION_CREDENTIAL_VARIABLES


def test_installation_environment_clears_the_import_path_and_publication_credentials() -> None:
    polluted = {
        "PATH": "/usr/bin",
        "PYTHONPATH": f"{REPOSITORY_ROOT / 'src'}:{REPOSITORY_ROOT}",
        "PYTHONHOME": "/opt/python",
        "TWINE_USERNAME": "__token__",
        "TWINE_PASSWORD": "publication-credential",
        "UV_PUBLISH_TOKEN": "publication-credential",
        "PYPI_API_TOKEN": "publication-credential",
    }

    prepared = install_verification.installation_environment(polluted)

    assert prepared["PATH"] == "/usr/bin"
    assert "PYTHONPATH" not in prepared
    assert "PYTHONHOME" not in prepared
    assert not set(prepared) & install_verification.PUBLICATION_CREDENTIAL_VARIABLES
    assert "publication-credential" not in "\n".join(prepared.values())
    assert str(REPOSITORY_ROOT) not in "\n".join(prepared.values())


def test_verification_refuses_a_workspace_inside_the_source_checkout() -> None:
    with pytest.raises(ValueError, match="outside"):
        install_verification.verify_installation(
            root=REPOSITORY_ROOT,
            workspace=REPOSITORY_ROOT / "build" / "install-verification",
            python_version="3.12",
        )


def test_the_revision_under_test_is_the_current_head_commit() -> None:
    revision = install_verification.head_revision(REPOSITORY_ROOT)

    assert revision is not None
    assert FULL_SHA.fullmatch(revision)


def test_claimed_python_versions_are_declared() -> None:
    assert CLAIMED_PYTHON_VERSIONS == ("3.12", "3.13")


def test_unavailable_verification_records_an_explicit_skip_reason(tmp_path: Path) -> None:
    result = install_verification.verify_installation(
        root=REPOSITORY_ROOT,
        workspace=tmp_path / "workspace",
        python_version="3.12",
        repository="https://sdr-installation-verification.invalid/ign24/spec-driven-research",
    )
    record = result.to_dict()

    assert result.performed is False
    assert record["status"] == install_verification.STATUS_SKIPPED
    assert isinstance(record["skip_reason"], str) and record["skip_reason"].strip()
    assert str(record["revision"]) in str(record["skip_reason"])
    assert str(record["repository"]) in str(record["skip_reason"])
    assert record["terminal_status"] is None


def test_a_skipped_verification_is_distinguishable_from_an_absent_one(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sdr.install_verification",
            "--root",
            str(REPOSITORY_ROOT),
            "--workspace",
            str(tmp_path / "workspace"),
            "--python-version",
            "3.12",
            "--repository",
            "https://sdr-installation-verification.invalid/ign24/spec-driven-research",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY_ROOT,
    )
    record = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert set(record) >= {"status", "skip_reason", "repository", "revision", "python_version"}
    assert record["status"] == install_verification.STATUS_SKIPPED
    assert record["skip_reason"]
    assert record["status"] != install_verification.STATUS_PASSED


def test_the_installation_marker_is_registered_with_its_network_requirement() -> None:
    manifest = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = manifest["tool"]["pytest"]["ini_options"]["markers"]

    installation = [marker for marker in markers if marker.startswith("installation:")]
    assert len(installation) == 1
    assert "network" in installation[0]


@pytest.mark.installation
def test_pinned_revision_installs_outside_the_checkout(
    verification: install_verification.InstallVerification,
) -> None:
    record = verification.to_dict()

    assert record["status"] == install_verification.STATUS_PASSED, record["failure_reason"]
    assert record["revision"] == install_verification.head_revision(REPOSITORY_ROOT)
    assert str(record["requirement"]).endswith(f"@{record['revision']}")
    assert str(record["repository"]) in str(record["requirement"])

    module_path = Path(str(record["module_path"]))
    assert not module_path.is_relative_to(REPOSITORY_ROOT)
    assert record["import_path"]
    for entry in record["import_path"]:
        assert not Path(entry).resolve().is_relative_to(REPOSITORY_ROOT), entry


@pytest.mark.installation
def test_installed_console_script_completes_a_light_lifecycle(
    verification: install_verification.InstallVerification,
) -> None:
    record = verification.to_dict()

    assert record["status"] == install_verification.STATUS_PASSED, record["failure_reason"]
    assert record["terminal_status"] == "done"
    assert set(record["stages"]) == {"intake", "explore", "transfer", "reuse"}

    executed = [command["name"] for command in record["commands"]]
    assert "create-investigation" in executed
    assert "packaged-brief" in executed
    assert executed[-1] == "final-status"
    for command in record["commands"]:
        assert command["returncode"] == 0, command


@pytest.mark.installation
def test_investigation_is_created_from_the_packaged_brief_template(
    verification: install_verification.InstallVerification,
) -> None:
    record = verification.to_dict()
    packaged = next(
        command for command in record["commands"] if command["name"] == "packaged-brief"
    )

    assert packaged["returncode"] == 0
    assert verification.packaged_brief_digest
    assert verification.created_brief_digest == verification.packaged_brief_digest
