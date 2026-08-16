"""E2E: ciclo completo new -> intake -> explore -> probe -> transfer -> reuse -> done.

Ejercita gates, anclaje textual, verificación persistida de probe y aprobación
humana en transfer.
"""

import hashlib
import json
import sys

import yaml

from sdr import lifecycle, probe_verify
from sdr.research import Approval, Research
from sdr.verification_ledger import load_ledger


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def _write_snapshot(research, source_id, url, content):
    source_dir = research.artifact_path(f"notes/sources/{source_id}")
    source_dir.mkdir(parents=True)
    content_bytes = content.encode("utf-8")
    (source_dir / "content.md").write_bytes(content_bytes)
    metadata = {
        "schema_version": 2,
        "url": url,
        "declared_url": url,
        "final_url": url,
        "redirects": [],
        "captured_at": "2026-07-03T00:00:00+00:00",
        "content_hash": hashlib.sha256(content_bytes).hexdigest(),
        "http_status": 200,
        "content_type": "text/plain",
        "content_eligible": True,
        "status": "ok",
    }
    (source_dir / "meta.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
    )


def test_full_cycle_reaches_done(tmp_path):
    r = Research.create(
        base=tmp_path,
        slug="eval-rag",
        title="Evaluar RAG",
        question="¿Sirve RAG agentico para soporte?",
        mode="full",
        owner="nacho",
        timebox=5,
    )

    # --- intake ---
    _write(
        r.artifact_path("brief.md"),
        "---\nresearch: eval-rag\ndate: 2026-07-03\nstage: intake\nowner: nacho\ntimebox: 5\n---\n\n"
        "## Question\n¿RAG agentico mejora la resolucion de soporte?\n\n"
        "## Hypothesis\nCreemos que sube la precision.\n\n"
        "## Context\nSoporte con alto volumen.\n\n"
        "## Scope\nIncluye RAG. No incluye fine-tuning.\n\n"
        "## Evaluation criteria\n- C1: precision > 0.8\n- C2: latencia < 2s\n\n"
        "## Adoption risks\nCosto de indexado.\n",
    )
    res = lifecycle.advance(r)
    assert res.ok and r.meta.stage == "explore", res.blocked_reason

    # --- explore ---
    _write(
        r.root / "notes" / "landscape.md",
        "---\nresearch: eval-rag\ndate: 2026-07-03\nstage: explore\n"
        "sources:\n"
        "  - id: S1\n    url: https://docs.rag.dev/guide\n    tier: T1\n    date: 2026-01-01\n    alternative: rag-agentico\n"
        "  - id: S2\n    url: https://bench.example.org/rag\n    tier: T2\n    date: 2026-02-01\n    alternative: rag-agentico\n"
        "  - id: S3\n    url: https://docs.search.dev/guide\n    tier: T1\n    date: 2026-01-01\n    alternative: rag-clasico\n"
        "  - id: S4\n    url: https://bench.other.example/rag\n    tier: T2\n    date: 2026-02-01\n    alternative: rag-clasico\n"
        "---\n\n"
        "## Alternatives evaluated\nRAG clasico vs agentico [S1].\n\n"
        "## Maturity\nEstable [S3].\n\n## Costs\nModerado [S2].\n\n## Risks\nDrift de indice.\n\n"
        "## Counter-evidence\nSe buscaron benchmarks negativos y no aparecieron señales fuertes [S4].\n",
    )
    snapshots = {
        "S1": "RAG clasico vs agentico.",
        "S2": "Moderado.",
        "S3": "Estable.",
        "S4": "Se buscaron benchmarks negativos y no aparecieron señales fuertes.",
    }
    urls = {
        "S1": "https://docs.rag.dev/guide",
        "S2": "https://bench.example.org/rag",
        "S3": "https://docs.search.dev/guide",
        "S4": "https://bench.other.example/rag",
    }
    for source_id, content in snapshots.items():
        _write_snapshot(r, source_id, urls[source_id], content)
        source_dir = r.artifact_path(f"notes/sources/{source_id}")
        metadata = yaml.safe_load((source_dir / "meta.yaml").read_text(encoding="utf-8"))
        assert (
            metadata["content_hash"]
            == hashlib.sha256((source_dir / "content.md").read_bytes()).hexdigest()
        )
    res = lifecycle.advance(r, offline=True)
    assert res.ok and r.meta.stage == "probe", res.blocked_reason

    # --- probe ---
    _write(r.artifact_path("probe/metrics.json"), '{"precision": 0.86, "latency": 1.4}')
    _write(r.artifact_path("probe/bench.py"), "print('OK precision 0.86 latency 1.4')\n")
    _write(
        r.artifact_path("probe/results.md"),
        f"---\nresearch: eval-rag\ndate: 2026-07-03\nstage: probe\nverify:\n  action: run\n  argv: {json.dumps([sys.executable, 'bench.py'])}\n  expect: OK\n---\n\n"
        "## Results by criterion\n"
        "| criterio | resultado | evidencia |\n|---|---|---|\n"
        "| C1 | cumple, precision 0.86 | `probe/metrics.json` |\n"
        "| C2 | cumple, 1.4s | `probe/metrics.json` |\n\n"
        "## Reproduction\n```bash\npython bench.py\n```\n",
    )
    verify = probe_verify.verify_probe(r, timeout=5)
    assert verify.passed
    res = lifecycle.advance(r)
    assert res.ok and r.meta.stage == "transfer", res.blocked_reason

    # --- transfer (con aprobación humana) ---
    claim_ids = [
        str(claim["claim_id"])
        for claim in load_ledger(r.artifact_path("notes/sources/verification.yaml"))["claims"]
        if claim.get("state") == "verified"
    ]
    evidence_claim_ids = "".join(f"  - {claim_id}\n" for claim_id in claim_ids)
    _write(
        r.artifact_path("decision-memo.md"),
        "---\nresearch: eval-rag\ndate: 2026-07-03\nstage: transfer\nring: trial\naudience: equipo\n"
        f"evidence_claim_ids:\n{evidence_claim_ids}"
        "---\n\n"
        "## Recommendation\nIn the context of support, facing the volume, we decide to pilot "
        "agentic RAG to achieve better precision, because the probe met C1 and C2, "
        "accepting a higher indexing cost.\n\n"
        "## Alternatives evaluated\nClassic RAG, fine-tuning.\n\n"
        "## Selection criteria\nPrecision and latency.\n\n"
        "## Risks and limitations\nCosto, drift.\n\n"
        "## Next steps\nPiloto 2 semanas.\n\n"
        "## Audience\nEquipo tecnico y direccion.\n",
    )
    # Without approval: blocked.
    blocked = lifecycle.advance(r)
    assert not blocked.ok and "approval" in blocked.blocked_reason.lower()

    # With human approval: advances to reuse.
    r.meta.approval = Approval(by="nacho", date="2026-07-03")
    r.save()
    res = lifecycle.advance(r)
    assert res.ok and r.meta.stage == "reuse", res.blocked_reason

    # --- reuse ---
    _write(
        r.root / "assets" / "post.md",
        "---\nresearch: eval-rag\ndate: 2026-07-03\nstage: reuse\ntype: post\naudience: external\n---\n\n"
        "RAG agentico subio la precision de soporte a 0.86 en nuestro piloto.\n",
    )
    res = lifecycle.advance(r)
    assert res.ok
    assert r.meta.status == "done"

    # La cadena de validación quedó registrada para todas las etapas del modo full.
    assert set(r.meta.validation) == {"intake", "explore", "probe", "transfer", "reuse"}
    # Y la evidencia sigue consistente (nada fue manipulado tras validar).
    assert lifecycle.check_consistency(r) == []
