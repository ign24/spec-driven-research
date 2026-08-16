import json
import os
import signal
import sys
import textwrap
import time

from click.testing import CliRunner

from sdr import gates, probe_verify
from sdr.cli import main
from sdr.research import Research


def _probe_research(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="t", question="q")
    r.artifact_path("brief.md").write_text(
        "## Evaluation criteria\n\n- C1: salida OK\n- C2: costo bajo\n", encoding="utf-8"
    )
    argv = json.dumps([sys.executable, "check.py"])
    r.artifact_path("probe/results.md").write_text(
        textwrap.dedent(
            f"""
            ---
            research: eval-foo
            date: 2026-07-03
            stage: probe
            verify:
              action: run
              argv: {argv}
              expect: OK
              environment: clean
            ---

            ## Results by criterion
            - C1: cumple - output OK
            - C2: cumple - costo bajo

            ## Reproduction
            ```bash
            python check.py
            ```
            """
        ).lstrip(),
        encoding="utf-8",
    )
    r.artifact_path("probe/check.py").write_text("print('OK')\n", encoding="utf-8")
    return r


def test_probe_v2_requires_verify_frontmatter(tmp_path):
    r = _probe_research(tmp_path)
    text = r.artifact_path("probe/results.md").read_text(encoding="utf-8")
    r.artifact_path("probe/results.md").write_text(
        text.replace(
            f"verify:\n  action: run\n  argv: {json.dumps([sys.executable, 'check.py'])}\n"
            "  expect: OK\n  environment: clean\n",
            "",
        ),
        encoding="utf-8",
    )

    report = gates.check_stage(r, stage="probe", offline=True)

    assert any(
        f.check == "benchmark_reproducible" and "verify" in f.detail for f in report.failures
    )


def test_probe_v2_rejects_non_list_argv(tmp_path):
    r = _probe_research(tmp_path)
    path = r.artifact_path("probe/results.md")
    text = path.read_text(encoding="utf-8").replace(
        f"argv: {json.dumps([sys.executable, 'check.py'])}", "argv: python check.py"
    )
    path.write_text(text, encoding="utf-8")

    report = gates.check_stage(r, stage="probe", offline=True)

    assert any(
        failure.check == "benchmark_reproducible" and "argv" in failure.detail
        for failure in report.failures
    )


def test_verify_probe_persists_pass_and_hash(tmp_path):
    r = _probe_research(tmp_path)

    result = probe_verify.verify_probe(r, timeout=5)

    assert result.passed
    assert r.meta.verify_probe["result"] == "pass"
    assert r.meta.verify_probe["probe_hash"]


def test_verify_probe_requires_explicit_run_action(tmp_path):
    r = _probe_research(tmp_path)
    path = r.artifact_path("probe/results.md")
    path.write_text(
        path.read_text(encoding="utf-8").replace("  action: run\n", ""), encoding="utf-8"
    )

    try:
        probe_verify.verify_probe(r, timeout=5)
    except ValueError as exc:
        assert "action" in str(exc)
    else:
        raise AssertionError("verify-probe aceptó una ejecución sin acción explícita")


def test_verify_probe_legacy_command_does_not_interpret_shell_metacharacters(tmp_path):
    r = _probe_research(tmp_path)
    path = r.artifact_path("probe/results.md")
    exploit = r.artifact_path("probe/exploit.py")
    exploit.write_text(
        "from pathlib import Path\nPath('PWNED').write_text('yes')\n", encoding="utf-8"
    )
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        f"  argv: {json.dumps([sys.executable, 'check.py'])}",
        f"  command: {sys.executable} check.py ; {sys.executable} exploit.py",
    )
    path.write_text(text, encoding="utf-8")

    result = probe_verify.verify_probe(r, timeout=5)

    assert result.passed
    assert not r.artifact_path("probe/PWNED").exists()


def test_verify_probe_records_confined_probe_cwd(tmp_path):
    r = _probe_research(tmp_path)

    result = probe_verify.verify_probe(r, timeout=5)

    assert result.cwd == str(r.artifact_path("probe").resolve())
    assert r.meta.verify_probe["cwd"] == result.cwd


def test_verify_probe_uses_declared_clean_environment(tmp_path, monkeypatch):
    r = _probe_research(tmp_path)
    monkeypatch.setenv("SDR_TEST_SECRET", "must-not-leak")
    r.artifact_path("probe/check.py").write_text(
        "import os\nprint('OK' if 'SDR_TEST_SECRET' not in os.environ else 'LEAKED')\n",
        encoding="utf-8",
    )

    result = probe_verify.verify_probe(r, timeout=5)

    assert result.passed
    assert result.environment == "clean"


def test_verify_probe_limits_captured_output(tmp_path):
    r = _probe_research(tmp_path)
    r.artifact_path("probe/check.py").write_text(
        "print('OK')\nprint('x' * 1_000_000)\n",
        encoding="utf-8",
    )

    result = probe_verify.verify_probe(r, timeout=5)

    assert result.passed
    assert result.output_truncated
    assert len(result.output.encode("utf-8")) <= result.output_limit_bytes + 32
    assert r.meta.verify_probe["output_truncated"] is True


def test_verify_probe_timeout_terminates_process_tree(tmp_path):
    r = _probe_research(tmp_path)
    probe_dir = r.artifact_path("probe")
    probe_dir.joinpath("child.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    probe_dir.joinpath("check.py").write_text(
        textwrap.dedent(
            """
            import os
            from pathlib import Path
            import subprocess
            import sys
            import time

            child = subprocess.Popen([sys.executable, "child.py"])
            Path("pids.txt").write_text(f"{os.getpid()} {child.pid}")
            print("STARTED", flush=True)
            time.sleep(60)
            """
        ).lstrip(),
        encoding="utf-8",
    )

    result = probe_verify.verify_probe(r, timeout=0.2)
    pids = [int(value) for value in probe_dir.joinpath("pids.txt").read_text().split()]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and any(_pid_exists(pid) for pid in pids):
        time.sleep(0.05)
    survivors = [pid for pid in pids if _pid_exists(pid)]
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    assert result.timed_out
    assert result.error_code == "timeout"
    assert not survivors


def test_verify_probe_persists_hash_after_generated_artifacts(tmp_path):
    r = _probe_research(tmp_path)
    r.artifact_path("probe/check.py").write_text(
        "from pathlib import Path\nPath('generated.txt').write_text('evidence')\nprint('OK')\n",
        encoding="utf-8",
    )

    result = probe_verify.verify_probe(r, timeout=5)

    assert result.passed
    assert result.probe_hash == probe_verify.hash_probe_dir(r)
    assert r.meta.verify_probe["probe_hash"] == probe_verify.hash_probe_dir(r)


def test_verify_probe_fails_when_expect_missing(tmp_path):
    r = _probe_research(tmp_path)
    text = r.artifact_path("probe/results.md").read_text(encoding="utf-8")
    r.artifact_path("probe/results.md").write_text(
        text.replace("expect: OK", "expect: MISSING"), encoding="utf-8"
    )

    result = probe_verify.verify_probe(r, timeout=5)

    assert not result.passed
    assert r.meta.verify_probe["result"] == "fail"


def test_ring_requires_current_verify_probe_hash(tmp_path):
    r = _probe_research(tmp_path)
    r.meta.validation["probe"] = "passed"
    probe_verify.verify_probe(r, timeout=5)
    r.meta.stage = "transfer"
    r.artifact_path("decision-memo.md").write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: transfer\nring: adopt\naudience: equipo\n---\n\n"
        "## Recommendation\nDecidimos adoptar Foo para soporte porque C1 y C2 cumplen, aceptando el trade-off de lock-in.\n\n"
        "## Alternatives evaluated\nFoo.\n\n## Selection criteria\nC1, C2.\n\n"
        "## Risks and limitations\nLock-in.\n\n## Next steps\nPiloto.\n\n## Audience\nEquipo.\n",
        encoding="utf-8",
    )

    ok = gates.check_stage(r, stage="transfer", offline=True)
    r.artifact_path("probe/check.py").write_text("print('OK changed')\n", encoding="utf-8")
    stale = gates.check_stage(r, stage="transfer", offline=True)

    assert not any(f.check == "ring_backed_by_evidence" and not f.passed for f in ok.results)
    assert any(
        f.check == "ring_backed_by_evidence" and "verify-probe" in f.detail for f in stale.failures
    )


def test_verify_probe_cli_json(tmp_path, monkeypatch):
    _probe_research(tmp_path)
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))

    result = CliRunner().invoke(
        main, ["verify-probe", "eval-foo", "--json"], catch_exceptions=False
    )

    assert result.exit_code == 0
    assert '"result": "pass"' in result.output


def _pid_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
