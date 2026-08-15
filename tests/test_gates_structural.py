import textwrap

from sdr import gates
from sdr.research import Research


def _make(tmp_path, mode="full"):
    return Research.create(
        base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?", mode=mode
    )


_BRIEF_TEMPLATE = textwrap.dedent(
    """
    ---
    research: eval-foo
    date: 2026-07-03
    stage: intake
    owner: nacho
    timebox: 3
    ---

    ## Question
    ¿Sirve Foo?

    ## Hypothesis
    Creemos que sí.

    ## Context
    Lo necesitamos para X.

    ## Scope
    Incluye A. No incluye B.

    ## Evaluation criteria
    __CRITERIA__

    ## Adoption risks
    Vendor lock-in.
    """
).lstrip()


def _brief(criteria_lines: str) -> str:
    return _BRIEF_TEMPLATE.replace("__CRITERIA__", criteria_lines)


def test_intake_gate_passes_with_complete_brief(tmp_path):
    r = _make(tmp_path)
    r.artifact_path("brief.md").write_text(
        _brief("- C1: latencia < 200ms\n- C2: costo < 10 USD/mes"), encoding="utf-8"
    )
    report = gates.check_stage(r, stage="intake")
    assert report.passed, report.failures


def test_intake_gate_fails_when_brief_missing(tmp_path):
    r = _make(tmp_path)
    report = gates.check_stage(r, stage="intake")
    assert not report.passed
    assert any("brief.md" in f.detail for f in report.failures)


def test_intake_gate_fails_with_empty_section(tmp_path):
    r = _make(tmp_path)
    body = _brief("- C1: x\n- C2: y").replace("¿Sirve Foo?", "")
    r.artifact_path("brief.md").write_text(body, encoding="utf-8")
    report = gates.check_stage(r, stage="intake")
    assert not report.passed
    assert any("Question" in f.detail for f in report.failures)


def test_intake_gate_fails_with_missing_frontmatter_field(tmp_path):
    r = _make(tmp_path)
    body = _brief("- C1: x\n- C2: y").replace("owner: nacho\n", "")
    r.artifact_path("brief.md").write_text(body, encoding="utf-8")
    report = gates.check_stage(r, stage="intake")
    assert not report.passed
    assert any("owner" in f.detail for f in report.failures)


def test_intake_gate_fails_with_single_criterion(tmp_path):
    r = _make(tmp_path)
    r.artifact_path("brief.md").write_text(_brief("- C1: solo uno"), encoding="utf-8")
    report = gates.check_stage(r, stage="intake")
    assert not report.passed
    assert any("criteria" in f.detail.lower() for f in report.failures)
