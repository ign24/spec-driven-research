"""Contract tests for isolated cross-investigation reuse fixtures."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import stat
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from bench.harness.corpus import CorpusItem
from bench.harness.runspace import REPOSITORY_ROOT
from sdr.cli import main
from sdr.cross_investigation import derive_cross_investigation_layer


def _reuse_module():
    return importlib.import_module("bench.harness.reuse")


def _fixture_root() -> Path:
    return REPOSITORY_ROOT / "bench" / "reuse-corpus"


def _copied_corpus(tmp_path: Path) -> Path:
    target = tmp_path / "reuse-corpus"
    shutil.copytree(_fixture_root(), target)
    return target


def _scenario_path(root: Path, scenario_id: str) -> Path:
    return root / "scenarios" / f"{scenario_id}.yaml"


def _rewrite_scenario(root: Path, scenario_id: str, transform) -> None:
    path = _scenario_path(root, scenario_id)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    transform(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _rewrite_artifact(
    root: Path, scenario_id: str, investigation_id: str, relative: str, transform
):
    scenario_path = _scenario_path(root, scenario_id)
    scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    investigations = [*scenario["seeds"], scenario["focal"]]
    investigation = next(item for item in investigations if item["id"] == investigation_id)
    artifact_path = root / investigation["source"] / relative
    transform(artifact_path)
    new_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    source = investigation["source"]
    for manifest_path in (root / "scenarios").glob("*.yaml"):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        changed = False
        for item in [*manifest["seeds"], manifest["focal"]]:
            if item["source"] != source:
                continue
            artifact = next(entry for entry in item["artifacts"] if entry["path"] == relative)
            artifact["sha256"] = new_hash
            changed = True
        if changed:
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def _project(payload, declaration):
    if isinstance(declaration, Mapping):
        return {key: _project(payload[key], value) for key, value in declaration.items()}
    if isinstance(declaration, list):
        return [
            _project(actual, expected)
            for actual, expected in zip(payload, declaration, strict=True)
        ]
    return payload


def _negative_is_absent(payload: dict, absence) -> bool:
    value = payload
    for segment in absence.path:
        value = value[segment]
    return absence.record not in value


def test_reuse_scenarios_use_a_separate_discriminated_schema() -> None:
    reuse = _reuse_module()

    corpus = reuse.load_reuse_corpus(_fixture_root())

    assert corpus.scenarios
    assert all(isinstance(scenario, reuse.ReuseScenario) for scenario in corpus.scenarios)
    assert all(scenario.kind == "cross-investigation-reuse" for scenario in corpus.scenarios)
    assert not any(isinstance(scenario, CorpusItem) for scenario in corpus.scenarios)


def test_reuse_fixture_declares_immutable_done_seeds_one_focal_and_orthogonal_factors() -> None:
    reuse = _reuse_module()

    corpus = reuse.load_reuse_corpus(_fixture_root())

    for scenario in corpus.scenarios:
        assert scenario.seeds
        assert all(seed.role == "seed" and seed.status == "done" for seed in scenario.seeds)
        assert scenario.focal.role == "focal"
        assert scenario.focal.id not in {seed.id for seed in scenario.seeds}
        assert scenario.arm in {"baseline", "light", "full"}
        assert scenario.history in {"history-present", "history-absent"}
        assert scenario.history not in {"baseline", "light", "full"}


@pytest.mark.parametrize(
    ("transform", "message"),
    [
        (lambda data: data.update(kind="lifecycle-planted-defect"), "kind"),
        (lambda data: data.update(seeds=[]), "seed"),
        (lambda data: data["seeds"][0].update(status="active"), "done"),
        (lambda data: data.update(focals=[data.pop("focal")]), "focal"),
        (lambda data: data.update(arm="history-present"), "arm"),
        (lambda data: data.update(history="light"), "history"),
    ],
)
def test_reuse_fixture_rejects_invalid_isolation_and_factor_contract(
    tmp_path: Path, transform, message: str
) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)
    scenario_id = "software-shared-source"
    _rewrite_scenario(root, scenario_id, transform)

    with pytest.raises(reuse.ReuseCorpusError, match=message):
        reuse.load_reuse_corpus(root)


def test_reuse_corpus_requires_current_provenance_lineage_and_exact_controls() -> None:
    reuse = _reuse_module()

    corpus = reuse.load_reuse_corpus(_fixture_root())

    assert any(scenario.domain != "software-engineering" for scenario in corpus.scenarios)
    for scenario in corpus.scenarios:
        assert scenario.category
        assert scenario.positive_expectations
        assert scenario.negative_controls
        for investigation in (*scenario.seeds, scenario.focal):
            assert investigation.schema_version == 2
            assert investigation.artifacts
            assert all(artifact.sha256 for artifact in investigation.artifacts)
        for seed in scenario.seeds:
            assert seed.evidence_claim_ids
        for control in scenario.negative_controls:
            assert control.absent
            assert all(item.path for item in control.absent)
            assert all(item.record for item in control.absent)


def test_positive_expectations_use_command_specific_canonical_projection_types() -> None:
    reuse = _reuse_module()

    corpus = reuse.load_reuse_corpus(_fixture_root())
    expectations = [
        expectation
        for scenario in corpus.scenarios
        for expectation in scenario.positive_expectations
    ]

    assert any(
        isinstance(expectation.projection, reuse.SourceDependencyProjection)
        for expectation in expectations
    )
    assert any(
        isinstance(expectation.projection, reuse.DeriveProjection) for expectation in expectations
    )
    for expectation in expectations:
        projection = expectation.projection.to_dict()
        assert all(value not in (None, "", [], {}) for value in projection.values())
        if expectation.command[:2] == ("cross", "source"):
            assert set(projection) == {
                "source_identity",
                "citations",
                "claims",
                "lineage_statuses",
                "dependent_decisions",
            }
        else:
            assert expectation.command == ("cross", "derive", "--json")
            assert set(projection) == {"resolver_chain", "joins"}
            assert all(join["provenance"] == "explicit" for join in projection["joins"])


@pytest.mark.parametrize(
    "field",
    ["citations", "claims", "lineage_statuses", "dependent_decisions"],
)
def test_source_positive_rejects_omitted_or_empty_canonical_records(
    tmp_path: Path, field: str
) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)

    def remove_field(data: dict) -> None:
        projection = data["positive_expectations"][0]["projection"]
        projection.pop(field)

    _rewrite_scenario(root, "software-shared-source", remove_field)
    with pytest.raises(reuse.ReuseCorpusError, match=field):
        reuse.load_reuse_corpus(root)

    root = _copied_corpus(tmp_path / "empty")
    _rewrite_scenario(
        root,
        "software-shared-source",
        lambda data: data["positive_expectations"][0]["projection"].update({field: []}),
    )
    with pytest.raises(reuse.ReuseCorpusError, match=field):
        reuse.load_reuse_corpus(root)


def test_duplicate_check_ids_are_rejected_across_all_declarations(tmp_path: Path) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)

    _rewrite_scenario(
        root,
        "software-shared-source",
        lambda data: data["negative_controls"][0].update(id=data["positive_expectations"][0]["id"]),
    )

    with pytest.raises(reuse.ReuseCorpusError, match="check.*id.*unique"):
        reuse.load_reuse_corpus(root)


def test_duplicate_check_ids_are_rejected_across_scenarios(tmp_path: Path) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)
    software = yaml.safe_load(
        _scenario_path(root, "software-shared-source").read_text(encoding="utf-8")
    )

    _rewrite_scenario(
        root,
        "horticulture-shared-source",
        lambda data: data["positive_expectations"][0].update(
            id=software["negative_controls"][0]["id"]
        ),
    )

    with pytest.raises(reuse.ReuseCorpusError, match="check.*id.*unique"):
        reuse.load_reuse_corpus(root)


def test_shared_argv_is_allowed_when_check_ids_are_unique() -> None:
    corpus = _reuse_module().load_reuse_corpus(_fixture_root())
    scenario = corpus.by_id("software-shared-source")

    assert scenario.positive_expectations[0].command == scenario.negative_controls[0].command
    assert scenario.positive_expectations[0].id != scenario.negative_controls[0].id


@pytest.mark.parametrize(
    ("transform", "message"),
    [
        (
            lambda data: data["seeds"][0]["artifacts"][1].update(sha256="0" * 64),
            "persisted-byte hash",
        ),
        (
            lambda data: data["seeds"][0].update(evidence_claim_ids=[]),
            "evidence_claim_ids",
        ),
        (lambda data: data.update(positive_expectations=[]), "positive"),
        (
            lambda data: data["positive_expectations"][0].update(projection={}),
            "projection",
        ),
        (lambda data: data.update(negative_controls=[]), "negative"),
        (
            lambda data: data["negative_controls"][0].update(absent=[]),
            "absent",
        ),
        (
            lambda data: data["negative_controls"][0]["absent"][0].update(path="source_merges"),
            "path",
        ),
        (
            lambda data: data["negative_controls"][0]["absent"][0].update(record={}),
            "record",
        ),
        (
            lambda data: data["negative_controls"][0]["absent"][0].update(
                record={"investigation": "software-seed"}
            ),
            "shape",
        ),
    ],
)
def test_reuse_fixture_rejects_invalid_provenance_lineage_or_controls(
    tmp_path: Path, transform, message: str
) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)
    scenario_id = "software-shared-source"
    _rewrite_scenario(root, scenario_id, transform)

    with pytest.raises(reuse.ReuseCorpusError, match=message):
        reuse.load_reuse_corpus(root)


def test_materializer_uses_one_external_root_with_only_the_focal_writable(tmp_path: Path) -> None:
    reuse = _reuse_module()
    scenario = reuse.load_reuse_corpus(_fixture_root()).by_id("software-shared-source")
    prepared = reuse.prepare_reuse_scenario(scenario, repetition=3, parent=tmp_path)

    with prepared as materialized:
        assert materialized.path.is_dir()
        assert materialized.research_root.is_dir()
        assert not materialized.path.is_relative_to(REPOSITORY_ROOT)
        assert set(materialized.seed_roots) == {seed.id for seed in scenario.seeds}
        assert materialized.focal_root.name == scenario.focal.id
        assert materialized.focal_root.stat().st_mode & stat.S_IWUSR
        for seed_root in materialized.seed_roots.values():
            assert not seed_root.stat().st_mode & stat.S_IWUSR
            assert materialized.pre_materialized_seed_hashes[seed_root.name]
        assert materialized.verify_seed_hashes() == materialized.pre_materialized_seed_hashes
        path = materialized.path

    assert not path.exists()
    assert prepared.evidence is not None
    assert (
        prepared.evidence.pre_materialized_seed_hashes
        == prepared.evidence.post_materialized_seed_hashes
    )
    assert prepared.evidence.pre_declared_seed_hashes == prepared.evidence.post_declared_seed_hashes
    assert prepared.evidence.unchanged is True
    assert prepared.evidence.cleanup_deleted is True
    assert prepared.evidence.cleanup_error is None


def test_materialized_roots_are_disjoint_across_scenarios_and_repetitions(tmp_path: Path) -> None:
    reuse = _reuse_module()
    corpus = reuse.load_reuse_corpus(_fixture_root())
    first = reuse.prepare_reuse_scenario(corpus.scenarios[0], repetition=0, parent=tmp_path)
    second = reuse.prepare_reuse_scenario(corpus.scenarios[0], repetition=1, parent=tmp_path)
    third = reuse.prepare_reuse_scenario(corpus.scenarios[1], repetition=0, parent=tmp_path)

    with first as first_root, second as second_root, third as third_root:
        paths = (first_root.path, second_root.path, third_root.path)
        assert len(set(paths)) == 3
        assert all(
            not left.is_relative_to(right) for left in paths for right in paths if left != right
        )


def test_history_absent_omits_seeds_without_mutating_fixture_source(tmp_path: Path) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)
    scenario = reuse.load_reuse_corpus(root).by_id("software-shared-source")
    scenario = replace(scenario, history="history-absent")
    fixture_before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    prepared = reuse.prepare_reuse_scenario(scenario, repetition=0, parent=tmp_path)

    with prepared as materialized:
        assert materialized.seed_roots == {}
        assert {path.name for path in materialized.research_root.iterdir()} == {scenario.focal.id}

    fixture_after = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert fixture_after == fixture_before
    assert prepared.evidence is not None
    assert prepared.evidence.pre_materialized_seed_hashes == {}
    assert prepared.evidence.post_materialized_seed_hashes == {}
    assert set(prepared.evidence.pre_declared_seed_hashes) == {"software-seed"}
    assert prepared.evidence.pre_declared_seed_hashes == prepared.evidence.post_declared_seed_hashes
    assert prepared.evidence.unchanged is True
    assert prepared.evidence.cleanup_deleted is True


def test_materializer_rejects_repository_parent() -> None:
    reuse = _reuse_module()
    scenario = reuse.load_reuse_corpus(_fixture_root()).scenarios[0]

    with pytest.raises(reuse.ReuseMaterializationError, match="outside the repository"):
        reuse.prepare_reuse_scenario(
            scenario,
            repetition=0,
            parent=REPOSITORY_ROOT / "bench",
        )


def test_completed_seed_rejects_stale_transfer_validation(tmp_path: Path) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)

    _rewrite_artifact(
        root,
        "software-shared-source",
        "software-seed",
        "decision-memo.md",
        lambda path: path.write_text(
            path.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8"
        ),
    )

    with pytest.raises(reuse.ReuseCorpusError, match="transfer.*hash consistency"):
        reuse.load_reuse_corpus(root)


def test_completed_seed_rejects_missing_transfer_validation(tmp_path: Path) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)

    def remove_transfer(path: Path) -> None:
        metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
        metadata["validation"].pop("transfer")
        path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    _rewrite_artifact(
        root,
        "software-shared-source",
        "software-seed",
        "sdr.yaml",
        remove_transfer,
    )

    with pytest.raises(reuse.ReuseCorpusError, match="requires transfer validation"):
        reuse.load_reuse_corpus(root)


def test_snapshot_url_must_match_its_note_source_declaration(tmp_path: Path) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)

    def change_note_url(path: Path) -> None:
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "https://docs.queue-lab.example/retry-window",
                "https://docs.queue-lab.example/different-source",
            ),
            encoding="utf-8",
        )

    _rewrite_artifact(
        root,
        "software-shared-source",
        "software-seed",
        "notes/landscape.md",
        change_note_url,
    )

    with pytest.raises(reuse.ReuseCorpusError, match="snapshot.*source declaration"):
        reuse.load_reuse_corpus(root)


def test_snapshot_requires_complete_canonical_provenance(tmp_path: Path) -> None:
    reuse = _reuse_module()
    root = _copied_corpus(tmp_path)

    def remove_content_type(path: Path) -> None:
        metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
        metadata.pop("content_type")
        path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    _rewrite_artifact(
        root,
        "software-shared-source",
        "software-seed",
        "notes/sources/S1/meta.yaml",
        remove_content_type,
    )

    with pytest.raises(reuse.ReuseCorpusError, match="current provenance"):
        reuse.load_reuse_corpus(root)


def test_negative_controls_are_exact_absences_in_cross_source_schema(tmp_path: Path) -> None:
    reuse = _reuse_module()
    corpus = reuse.load_reuse_corpus(_fixture_root())

    for index, scenario in enumerate(corpus.scenarios):
        prepared = reuse.prepare_reuse_scenario(scenario, repetition=index, parent=tmp_path)
        with prepared as materialized:
            runner = CliRunner()
            for control in scenario.negative_controls:
                result = runner.invoke(
                    main,
                    list(control.command),
                    env={"SDR_ROOT": str(materialized.research_root)},
                )
                assert result.exit_code == 0, result.output
                payload = json.loads(result.output)
                for absence in control.absent:
                    assert _negative_is_absent(payload, absence)
                    injected = json.loads(result.output)
                    target = injected
                    for segment in absence.path:
                        target = target[segment]
                    target.append(dict(absence.record))
                    assert not _negative_is_absent(injected, absence)


def test_same_topic_without_explicit_edge_has_exact_mutation_sensitive_absent_join(
    tmp_path: Path,
) -> None:
    reuse = _reuse_module()
    scenario = reuse.load_reuse_corpus(_fixture_root()).by_id("software-same-topic-no-edge")
    prepared = reuse.prepare_reuse_scenario(scenario, repetition=0, parent=tmp_path)

    with prepared as materialized:
        control = scenario.negative_controls[0]
        assert control.command == ("cross", "derive", "--json")
        result = CliRunner().invoke(
            main,
            list(control.command),
            env={"SDR_ROOT": str(materialized.research_root)},
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["joins"] == []
        for absence in control.absent:
            assert absence.path == ("joins",)
            assert _negative_is_absent(payload, absence)
            injected = json.loads(result.output)
            injected["joins"].append(dict(absence.record))
            assert not _negative_is_absent(injected, absence)


def test_every_positive_command_matches_actual_cross_cli_projection(tmp_path: Path) -> None:
    reuse = _reuse_module()
    corpus = reuse.load_reuse_corpus(_fixture_root())

    for index, scenario in enumerate(corpus.scenarios):
        prepared = reuse.prepare_reuse_scenario(scenario, repetition=index, parent=tmp_path)
        with prepared as materialized:
            runner = CliRunner()
            for expectation in scenario.positive_expectations:
                result = runner.invoke(
                    main,
                    list(expectation.command),
                    env={"SDR_ROOT": str(materialized.research_root)},
                )
                assert result.exit_code == 0, result.output
                payload = json.loads(result.output)
                declaration = expectation.projection.to_dict()
                assert _project(payload, declaration) == declaration
                direct = derive_cross_investigation_layer(materialized.research_root).to_dict()
                if expectation.command == ("cross", "derive", "--json"):
                    assert payload == direct


@pytest.mark.parametrize("unreadable", [False, True])
def test_actor_symlink_is_rejected_and_cleaned_without_touching_external_target(
    tmp_path: Path, unreadable: bool
) -> None:
    reuse = _reuse_module()
    scenario = reuse.load_reuse_corpus(_fixture_root()).by_id("software-shared-source")
    prepared = reuse.prepare_reuse_scenario(scenario, repetition=0, parent=tmp_path)
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    external_mode = 0 if unreadable else stat.S_IRUSR
    external.chmod(external_mode)
    external_lstat = external.lstat()

    with pytest.raises(reuse.ReuseMaterializationError, match="symlink"):
        with prepared as materialized:
            seed_root = next(iter(materialized.seed_roots.values()))
            seed_root.chmod(seed_root.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
            os.symlink(external, seed_root / "actor-link")
            scenario_path = materialized.path

    assert not scenario_path.exists()
    assert stat.S_IMODE(external.lstat().st_mode) == stat.S_IMODE(external_lstat.st_mode)
    external.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert external.read_bytes() == b"outside"
    assert prepared.evidence is not None
    assert prepared.evidence.cleanup_deleted is True
    assert prepared.evidence.cleanup_error is None


def test_actor_intermediate_symlink_is_rejected_without_traversing_external_tree(
    tmp_path: Path,
) -> None:
    reuse = _reuse_module()
    scenario = reuse.load_reuse_corpus(_fixture_root()).by_id("software-shared-source")
    prepared = reuse.prepare_reuse_scenario(scenario, repetition=0, parent=tmp_path)
    external = tmp_path / "external-tree"
    external_seed = external / "software-seed"
    external_seed.mkdir(parents=True)
    external_file = external_seed / "outside.txt"
    external_file.write_text("outside", encoding="utf-8")
    external_mode = external_file.lstat().st_mode

    with pytest.raises(reuse.ReuseMaterializationError, match="symlink"):
        with prepared as materialized:
            original = materialized.path / "research-original"
            materialized.research_root.rename(original)
            os.symlink(external, materialized.research_root)
            scenario_path = materialized.path

    assert not scenario_path.exists()
    assert external_file.read_text(encoding="utf-8") == "outside"
    assert external_file.lstat().st_mode == external_mode
    assert prepared.evidence is not None
    assert prepared.evidence.cleanup_deleted is True
    assert prepared.evidence.cleanup_error is None
