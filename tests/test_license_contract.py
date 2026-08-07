import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIT_LICENSE = """MIT License

Copyright (c) 2026 Ignacio Zúñiga Navarro

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
MIT_CLASSIFIER = "License :: OSI Approved :: MIT License"


@pytest.fixture(scope="module")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    dist = tmp_path_factory.mktemp("license-dist")
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(dist), str(PROJECT_ROOT)],
        check=True,
    )
    return next(dist.glob("*.whl")), next(dist.glob("*.tar.gz"))


def test_root_license_is_canonical_mit_text() -> None:
    assert (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8") == MIT_LICENSE


def test_pyproject_declares_mit_spdx_and_classifier() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert project["license"] == "MIT"
    assert MIT_CLASSIFIER in project["classifiers"]
    assert not any("Apache" in classifier for classifier in project["classifiers"])


def test_wheel_metadata_and_license_payload_are_mit(
    distributions: tuple[Path, Path],
) -> None:
    wheel, _ = distributions
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        license_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/licenses/LICENSE")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        license_text = archive.read(license_name).decode("utf-8")

    assert metadata["License-Expression"] == "MIT"
    assert MIT_CLASSIFIER in metadata.get_all("Classifier", [])
    assert license_text == MIT_LICENSE


def test_sdist_metadata_and_license_payload_are_mit(
    distributions: tuple[Path, Path],
) -> None:
    _, sdist = distributions
    with tarfile.open(sdist, "r:gz") as archive:
        metadata_member = next(
            member for member in archive.getmembers() if member.name.endswith("/PKG-INFO")
        )
        license_member = next(
            member for member in archive.getmembers() if member.name.endswith("/LICENSE")
        )
        metadata_file = archive.extractfile(metadata_member)
        license_file = archive.extractfile(license_member)
        assert metadata_file is not None
        assert license_file is not None
        metadata = BytesParser().parsebytes(metadata_file.read())
        license_text = license_file.read().decode("utf-8")

    assert metadata["License-Expression"] == "MIT"
    assert MIT_CLASSIFIER in metadata.get_all("Classifier", [])
    assert license_text == MIT_LICENSE


@pytest.mark.parametrize(
    "relative_path",
    [
        "openspec/specs/public-repository-boundary/spec.md",
        "openspec/changes/release-hardening-and-public-distribution/design.md",
        "openspec/changes/release-hardening-and-public-distribution/specs/public-repository-boundary/spec.md",
        "openspec/changes/archive/2026-08-02-switch-license-to-mit/specs/public-repository-boundary/spec.md",
    ],
)
def test_consolidated_and_active_openspec_contracts_require_mit(relative_path: str) -> None:
    text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    assert "MIT" in text
    assert "Apache-2.0 licensing" not in text
    assert "license is Apache-2.0" not in text
    assert "declared license is Apache-2.0" not in text


def test_bilingual_readmes_identify_mit() -> None:
    english = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    spanish = (PROJECT_ROOT / "README.es.md").read_text(encoding="utf-8")

    assert "[MIT License](LICENSE)" in english
    assert "[Licencia MIT](LICENSE)" in spanish


def test_unreleased_changelog_records_relicensing_without_revoking_historical_grants() -> None:
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("## [0.1.0]", 1)[0]

    assert "Current and future distributions are licensed under MIT" in unreleased
    assert "historical Apache-2.0 grants are not revoked" in unreleased
    assert "legal advice" not in unreleased.lower()


def test_current_public_license_claims_do_not_identify_apache() -> None:
    current_claims = [
        "LICENSE",
        "pyproject.toml",
        "README.md",
        "README.es.md",
        "openspec/specs/public-repository-boundary/spec.md",
        "openspec/changes/release-hardening-and-public-distribution/design.md",
        "openspec/changes/release-hardening-and-public-distribution/specs/public-repository-boundary/spec.md",
    ]
    combined = "\n".join(
        (PROJECT_ROOT / path).read_text(encoding="utf-8") for path in current_claims
    )

    assert "Apache-2.0" not in combined
    assert "Apache License" not in combined
