"""Motor de gates: capas estructural y evidencial de validación.

Evalúa las reglas declarativas del schema contra los artefactos de una etapa y
produce un reporte accionable pass/fail por regla. Los checks evidenciales
(tiers de fuentes, triangulación, enlaces, cross-reference brief-probe, etc.)
se registran por nombre y se resuelven desde `ArtifactSpec.checks`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlparse

import yaml

from sdr import schema
from sdr.claims import extract_claims, extract_references
from sdr.network_policy import NetworkPolicyError, fetch_http
from sdr.parser import Artifact, DuplicateFrontmatterKeyError, parse_artifact
from sdr.research import Research
from sdr.verification_ledger import (
    LedgerValidationError,
    load_ledger,
    validate_claim_references,
)

_CRITERION_ID_RE = re.compile(r"\bC\d+\b")
_CODE_FENCE_RE = re.compile(r"```")
_MARKDOWN_LINK_PROBE_RE = re.compile(r"\[[^\]]+\]\((probe/[^)\s]+)\)")
_INLINE_PROBE_PATH_RE = re.compile(r"`(probe/[^`\s]+)`")


@dataclass
class GateResult:
    check: str
    passed: bool
    detail: str = ""
    skipped: bool = False


@dataclass
class GateReport:
    stage: str
    results: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed or r.skipped for r in self.results)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed and not r.skipped]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "results": [
                {
                    "check": r.check,
                    "passed": r.passed,
                    "skipped": r.skipped,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }


@dataclass(frozen=True)
class SourceExpiry:
    """Mechanical age result under the investigation's configured tier policy."""

    tier: str
    source_date: date | None
    as_of: date
    max_age_days: int
    age_days: int | None
    expired: bool


def extract_criteria_ids(text: str) -> list[str]:
    """IDs de criterio (C1, C2, ...) únicos, en orden de aparición."""
    seen: dict[str, None] = {}
    for match in _CRITERION_ID_RE.findall(text or ""):
        seen.setdefault(match, None)
    return list(seen)


# ---------------------------------------------------------------------------
# Estructura
# ---------------------------------------------------------------------------


def _check_single_file(research: Research, spec: schema.ArtifactSpec) -> list[GateResult]:
    results: list[GateResult] = []
    path = research.artifact_path(spec.primary_file)
    if not path.exists():
        return [GateResult("structure", False, f"falta {spec.primary_file}")]
    try:
        art = parse_artifact(path)
    except DuplicateFrontmatterKeyError as exc:
        return [GateResult("duplicate_frontmatter_key", False, f"{spec.primary_file}: {exc}")]
    results.extend(_check_frontmatter(art, spec.frontmatter_required, spec.primary_file))
    results.extend(_check_sections(art, spec.required_sections, spec.primary_file))
    return results


def _check_collection(research: Research, spec: schema.ArtifactSpec) -> list[GateResult]:
    results: list[GateResult] = []
    directory = research.artifact_path(spec.collection_dir or "")
    files = sorted(directory.glob("*.md")) if directory.is_dir() else []
    if not files:
        return [GateResult("structure", False, f"{spec.collection_dir}/ no contiene artefactos")]
    arts = []
    for path in files:
        art = parse_artifact(path)
        rel = f"{spec.collection_dir}/{path.name}"
        results.extend(_check_frontmatter(art, spec.frontmatter_required, rel))
        arts.append(art)
    # Las secciones obligatorias se cubren en conjunto entre los artefactos.
    for section in spec.required_sections:
        if not any(art.has_content(section) for art in arts):
            results.append(
                GateResult("structure", False, f"ningún artefacto cubre la sección {section!r}")
            )
    return results


def _check_frontmatter(art: Artifact, required: tuple[str, ...], rel: str) -> list[GateResult]:
    results: list[GateResult] = []
    for field_name in required:
        value = art.frontmatter.get(field_name)
        if field_name == "evidence_claim_ids" and isinstance(value, list):
            continue
        if value in (None, "", [], {}):
            results.append(
                GateResult("frontmatter", False, f"{rel}: falta frontmatter {field_name!r}")
            )
    return results


def _check_sections(art: Artifact, required: tuple[str, ...], rel: str) -> list[GateResult]:
    results: list[GateResult] = []
    for section in required:
        if not art.has_content(section):
            results.append(
                GateResult("section", False, f"{rel}: sección {section!r} vacía o ausente")
            )
    return results


# ---------------------------------------------------------------------------
# Checks evidenciales (registro por nombre)
# ---------------------------------------------------------------------------


@dataclass
class CheckContext:
    research: Research
    stage: str
    offline: bool
    url_checker: Callable[[str], bool]


def _iter_sources(research: Research) -> list[dict[str, Any]]:
    """Normaliza las fuentes declaradas en las notas de explore."""
    directory = research.artifact_path("notes")
    sources: list[dict[str, Any]] = []
    if not directory.is_dir():
        return sources
    for path in sorted(directory.glob("*.md")):
        art = parse_artifact(path)
        for entry in art.frontmatter.get("sources", []) or []:
            if isinstance(entry, str):
                sources.append({"url": entry, "tier": None, "note": path.name})
            elif isinstance(entry, dict):
                sources.append({**entry, "note": path.name})
    return sources


_MULTI_LABEL_PUBLIC_SUFFIXES = {
    "com.ar",
    "com.br",
    "co.uk",
    "com.au",
    "co.jp",
}


def _hostname(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    host = netloc.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _registrable_domain(host: str) -> str:
    parts = [part for part in host.split(".") if part]
    if len(parts) < 2:
        return host
    suffix = ".".join(parts[-2:])
    if suffix in _MULTI_LABEL_PUBLIC_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return suffix


def _load_org_aliases(research: Research) -> dict[str, str]:
    path = research.artifact_path("notes/sources/orgs.yaml")
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    aliases = data.get("aliases", data) if isinstance(data, dict) else {}
    if not isinstance(aliases, dict):
        return {}
    return {str(key).lower(): str(value).lower() for key, value in aliases.items()}


def _org(url: str, aliases: dict[str, str] | None = None) -> str:
    aliases = aliases or {}
    host = _hostname(url)
    if not host:
        return ""
    parts = [part for part in host.split(".") if part]
    if host == "github.com" and len(urlparse(url).path.strip("/").split("/")) >= 1:
        owner = urlparse(url).path.strip("/").split("/")[0].lower()
        return aliases.get(owner, owner)
    for part in parts:
        if part in aliases:
            return aliases[part]
    registrable = _registrable_domain(host)
    org = registrable.split(".")[0]
    return aliases.get(org, org)


def _check_min_evaluation_criteria(ctx: CheckContext) -> list[GateResult]:
    brief = ctx.research.artifact_path("brief.md")
    if not brief.exists():
        return [GateResult("min_evaluation_criteria", False, "falta brief.md")]
    art = parse_artifact(brief)
    ids = extract_criteria_ids(art.section("Criterios de evaluación") or "")
    if len(ids) < schema.MIN_EVALUATION_CRITERIA:
        return [
            GateResult(
                "min_evaluation_criteria",
                False,
                f"se requieren al menos {schema.MIN_EVALUATION_CRITERIA} criterios "
                f"con ID (C1, C2, ...); se encontraron {len(ids)}",
            )
        ]
    return [GateResult("min_evaluation_criteria", True, f"{len(ids)} criterios")]


def _check_source_tiers(ctx: CheckContext) -> list[GateResult]:
    sources = _iter_sources(ctx.research)
    results: list[GateResult] = []
    for src in sources:
        tier = src.get("tier")
        if tier not in schema.SOURCE_TIERS:
            results.append(
                GateResult(
                    "source_tiers",
                    False,
                    f"{src.get('note')}: fuente {src.get('url')} sin tier válido (T1/T2/T3)",
                )
            )
    # Al menos una fuente T1 por alternativa declarada (o global si no se declara).
    by_alt: dict[str, list[str]] = {}
    for src in sources:
        by_alt.setdefault(src.get("alternative", "__global__"), []).append(src.get("tier"))
    for alt, tiers in by_alt.items():
        if "T1" not in tiers:
            label = "el conjunto" if alt == "__global__" else f"la alternativa {alt!r}"
            results.append(GateResult("source_tiers", False, f"{label} no tiene ninguna fuente T1"))
    if not results:
        results.append(GateResult("source_tiers", True, f"{len(sources)} fuentes con tier"))
    return results


def _check_source_dates(ctx: CheckContext) -> list[GateResult]:
    sources = _iter_sources(ctx.research)
    results: list[GateResult] = []
    brief_date, _ = _source_age_policy(ctx.research)
    for src in sources:
        if src.get("date") in (None, ""):
            results.append(
                GateResult(
                    "source_dates",
                    False,
                    f"{src.get('note')}: fuente {src.get('url')} sin date",
                )
            )
            continue
        if ctx.research.meta.schema_version >= 2 and brief_date:
            try:
                date.fromisoformat(str(src.get("date")))
            except ValueError:
                results.append(
                    GateResult(
                        "source_dates",
                        False,
                        f"{src.get('note')}: fuente {src.get('url')} con date inválida",
                    )
                )
                continue
            expiry = source_expiry(ctx.research, src, as_of=brief_date)
            if expiry.expired and not src.get("date_justification"):
                results.append(
                    GateResult(
                        "source_dates",
                        False,
                        f"{src.get('note')}: fuente {src.get('url')} vencida para {expiry.tier} "
                        f"({expiry.age_days} días > {expiry.max_age_days})",
                    )
                )
    if not results:
        results.append(GateResult("source_dates", True, f"{len(sources)} fuentes con date"))
    return results


def _source_age_policy(research: Research) -> tuple[date | None, dict[str, int]]:
    policy = dict(schema.DEFAULT_SOURCE_MAX_AGE)
    brief = research.artifact_path("brief.md")
    if not brief.exists():
        return None, policy
    fm = parse_artifact(brief).frontmatter
    custom = fm.get("source_max_age") or {}
    if isinstance(custom, dict):
        for tier, days in custom.items():
            try:
                policy[str(tier)] = int(days)
            except (TypeError, ValueError):
                continue
    try:
        return date.fromisoformat(str(fm.get("date"))), policy
    except (TypeError, ValueError):
        return None, policy


def source_expiry(research: Research, source: dict[str, Any], *, as_of: date) -> SourceExpiry:
    """Evaluate source age without changing the gate's configured tier policy."""
    _, policy = _source_age_policy(research)
    tier = str(source.get("tier") or "T3")
    allowed = policy.get(tier, schema.DEFAULT_SOURCE_MAX_AGE.get(tier, 180))
    try:
        source_date = date.fromisoformat(str(source.get("date")))
    except (TypeError, ValueError):
        source_date = None
    age = (as_of - source_date).days if source_date is not None else None
    return SourceExpiry(
        tier=tier,
        source_date=source_date,
        as_of=as_of,
        max_age_days=allowed,
        age_days=age,
        expired=age is not None and age > allowed,
    )


def _check_tier_plausibility(ctx: CheckContext) -> list[GateResult]:
    results: list[GateResult] = []
    for src in _iter_sources(ctx.research):
        declared = str(src.get("tier") or "")
        if declared not in schema.SOURCE_TIERS:
            continue
        derived = _derive_tier(src)
        if _tier_rank(declared) < _tier_rank(derived) and not src.get("tier_justification"):
            results.append(
                GateResult(
                    "tier_plausibility",
                    False,
                    f"{src.get('note')}: fuente {src.get('url')} declara {declared} "
                    f"pero deriva {derived}; agregue tier_justification si corresponde",
                )
            )
    if not results:
        results.append(GateResult("tier_plausibility", True, "tiers plausibles"))
    return results


def _derive_tier(src: dict[str, Any]) -> str:
    url = str(src.get("url") or "").lower()
    host = _hostname(url)
    alt = str(src.get("alternative") or "").lower()
    org = _org(url)
    if "doi.org" in host or "arxiv.org" in host:
        return "T1"
    if host == "github.com" and (not alt or org == alt):
        return "T1"
    if host.startswith("docs.") or host.startswith("developer.") or host.startswith("dev."):
        return "T1"
    if alt and org == alt:
        return "T1"
    if any(token in host for token in ("bench", "benchmark", "engineering")):
        return "T2"
    return "T3"


def _tier_rank(tier: str) -> int:
    return {"T1": 1, "T2": 2, "T3": 3}.get(tier, 99)


def _check_source_triangulation(ctx: CheckContext) -> list[GateResult]:
    sources = _iter_sources(ctx.research)
    has_alternatives = any(src.get("alternative") for src in sources)
    groups: dict[str, set[str]] = {}
    if has_alternatives:
        for src in sources:
            label = str(src.get("alternative") or "__sin_alternativa__")
            groups.setdefault(label, set())
            if src.get("url"):
                groups[label].add(_hostname(src["url"]))
    else:
        groups["__global__"] = {_hostname(s["url"]) for s in sources if s.get("url")}

    results: list[GateResult] = []
    for label, declared_hosts in groups.items():
        declared_hosts.discard("")
        if len(declared_hosts) < schema.MIN_DISTINCT_DECLARED_HOSTS:
            target = "el conjunto" if label == "__global__" else f"la alternativa {label!r}"
            results.append(
                GateResult(
                    "source_triangulation",
                    False,
                    f"{target} requiere >= {schema.MIN_DISTINCT_DECLARED_HOSTS} hosts declarados "
                    f"distintos; hay {len(declared_hosts)} hosts declarados distintos",
                )
            )
    if not results:
        total = sum(len(declared_hosts) for declared_hosts in groups.values())
        results.append(
            GateResult("source_triangulation", True, f"{total} hosts declarados distintos")
        )
    return results


def _check_links_resolve(ctx: CheckContext) -> list[GateResult]:
    if ctx.offline:
        return [GateResult("links_resolve", False, "omitido (--offline)", skipped=True)]
    results: list[GateResult] = []
    for src in _iter_sources(ctx.research):
        url = src.get("url")
        if url and not ctx.url_checker(url):
            results.append(
                GateResult("links_resolve", False, f"{src.get('note')}: enlace roto {url}")
            )
    if not results:
        results.append(GateResult("links_resolve", True, "enlaces verificados"))
    return results


def _check_claim_citation_coverage(ctx: CheckContext) -> list[GateResult]:
    directory = ctx.research.artifact_path("notes")
    files = sorted(directory.glob("*.md")) if directory.is_dir() else []
    declared = {str(src.get("id")) for src in _iter_sources(ctx.research) if src.get("id")}
    results: list[GateResult] = []
    for path in files:
        art = parse_artifact(path)
        markdown = path.read_text(encoding="utf-8")
        for reference in extract_references(markdown):
            if reference.source_id in declared:
                continue
            results.append(
                GateResult(
                    "claim_citation_coverage",
                    False,
                    f"{path.name}: marcador {reference.marker} sin fuente declarada "
                    f"{reference.source_id}",
                )
            )
        try:
            extract_claims(markdown, note_path=f"notes/{path.name}")
        except ValueError as exc:
            results.append(GateResult("claim_citation_coverage", False, str(exc)))
        for section in ("Alternativas evaluadas", "Madurez", "Costos"):
            content = art.section(section) or ""
            if content.strip() and not extract_references(content):
                results.append(
                    GateResult(
                        "claim_citation_coverage",
                        False,
                        f"{path.name}: sección {section!r} requiere al menos una cita [S<n>]",
                    )
                )
    if not results:
        results.append(GateResult("claim_citation_coverage", True, "citas inline cubiertas"))
    return results


def _check_criteria_cross_reference(ctx: CheckContext) -> list[GateResult]:
    brief = ctx.research.artifact_path("brief.md")
    results_file = ctx.research.artifact_path("probe/results.md")
    if not brief.exists() or not results_file.exists():
        return [GateResult("criteria_cross_reference", False, "falta brief.md o probe/results.md")]
    criteria_text = parse_artifact(brief).section("Criterios de evaluación") or ""
    brief_ids = set(extract_criteria_ids(criteria_text))
    results_text = results_file.read_text(encoding="utf-8")
    reported = set(extract_criteria_ids(results_text))
    missing = brief_ids - reported
    if missing:
        return [
            GateResult(
                "criteria_cross_reference",
                False,
                f"criterios del brief sin resultado en probe: {sorted(missing)}",
            )
        ]
    return [GateResult("criteria_cross_reference", True, f"{len(brief_ids)} criterios cubiertos")]


def _check_benchmark_reproducible(ctx: CheckContext) -> list[GateResult]:
    results_file = ctx.research.artifact_path("probe/results.md")
    if not results_file.exists():
        return [GateResult("benchmark_reproducible", False, "falta probe/results.md")]
    art = parse_artifact(results_file)
    repro = art.section("Reproducción") or ""
    fences = len(_CODE_FENCE_RE.findall(repro))
    if not repro.strip() or fences < 2:
        return [
            GateResult(
                "benchmark_reproducible",
                False,
                "la sección Reproducción debe incluir comandos/código en un bloque",
            )
        ]
    if ctx.research.meta.schema_version >= 2:
        verify = art.frontmatter.get("verify") or {}
        argv = verify.get("argv") if isinstance(verify, dict) else None
        command = verify.get("command") if isinstance(verify, dict) else None
        valid_argv = (
            isinstance(argv, list)
            and bool(argv)
            and all(isinstance(argument, str) for argument in argv)
        )
        valid_command = isinstance(command, str) and bool(command.strip())
        one_command = valid_argv != valid_command
        valid_environment = isinstance(verify, dict) and (
            verify.get("environment", "clean") in {"clean", "inherit"}
        )
        if (
            not isinstance(verify, dict)
            or verify.get("action") != "run"
            or not one_command
            or not verify.get("expect")
            or not valid_environment
        ):
            return [
                GateResult(
                    "benchmark_reproducible",
                    False,
                    "probe/results.md debe declarar verify: {action: run, argv, expect}",
                )
            ]
    return [GateResult("benchmark_reproducible", True, "prueba reproducible")]


def _check_probe_artifacts_exist(ctx: CheckContext) -> list[GateResult]:
    results_file = ctx.research.artifact_path("probe/results.md")
    if not results_file.exists():
        return [GateResult("probe_artifacts_exist", False, "falta probe/results.md")]
    text = results_file.read_text(encoding="utf-8")
    refs = set(_MARKDOWN_LINK_PROBE_RE.findall(text)) | set(_INLINE_PROBE_PATH_RE.findall(text))
    refs.discard("probe/results.md")
    missing = [ref for ref in sorted(refs) if not ctx.research.artifact_path(ref).exists()]
    if missing:
        return [
            GateResult(
                "probe_artifacts_exist",
                False,
                f"artefactos referenciados inexistentes: {missing}",
            )
        ]
    return [GateResult("probe_artifacts_exist", True, f"{len(refs)} artefactos referenciados")]


def _check_y_statement(ctx: CheckContext) -> list[GateResult]:
    memo = ctx.research.artifact_path("decision-memo.md")
    if not memo.exists():
        return [GateResult("y_statement", False, "falta decision-memo.md")]
    rec = (parse_artifact(memo).section("Recomendación") or "").lower()
    has_decision = "decidimos" in rec
    has_context = " para " in f" {rec} " or "contexto" in rec
    has_evidence = any(token in rec for token in ("porque", "evidencia", "basado en", "según"))
    has_downside = "aceptando" in rec and any(
        token in rec
        for token in ("trade-off", "riesgo", "limitación", "costo", "downside", "no adopt")
    )
    if not (has_decision and has_context and has_evidence and has_downside):
        return [
            GateResult(
                "y_statement",
                False,
                "la recomendación debe seguir el formato Y-statement "
                "(decidimos, contexto/para, evidencia/porque, aceptando trade-off)",
            )
        ]
    return [GateResult("y_statement", True, "recomendación en formato Y-statement")]


def _check_ring_backed_by_evidence(ctx: CheckContext) -> list[GateResult]:
    memo = ctx.research.artifact_path("decision-memo.md")
    if not memo.exists():
        return [GateResult("ring_backed_by_evidence", False, "falta decision-memo.md")]
    ring = str(parse_artifact(memo).frontmatter.get("ring", "")).lower()
    if ring not in schema.RECOMMENDATION_RINGS:
        return [
            GateResult(
                "ring_backed_by_evidence",
                False,
                f"ring inválido: {ring!r}; use uno de {schema.RECOMMENDATION_RINGS}",
            )
        ]
    mode = ctx.research.meta.mode
    if mode == "light" and ring in schema.RINGS_REQUIRING_PROBE:
        return [
            GateResult(
                "ring_backed_by_evidence",
                False,
                f"sin etapa probe (modo light) el anillo máximo es 'assess', no {ring!r}",
            )
        ]
    if ring in schema.RINGS_REQUIRING_PROBE and "probe" not in ctx.research.meta.validation:
        return [
            GateResult(
                "ring_backed_by_evidence",
                False,
                f"el anillo {ring!r} exige una etapa probe aprobada con resultados",
            )
        ]
    if ring in schema.RINGS_REQUIRING_PROBE and ctx.research.meta.schema_version >= 2:
        from sdr import probe_verify

        current_hash = probe_verify.hash_probe_dir(ctx.research)
        stored = ctx.research.meta.verify_probe or {}
        if stored.get("result") != "pass" or stored.get("probe_hash") != current_hash:
            return [
                GateResult(
                    "ring_backed_by_evidence",
                    False,
                    f"el anillo {ring!r} exige sdr verify-probe vigente y en verde",
                )
            ]
    return [GateResult("ring_backed_by_evidence", True, f"anillo {ring!r} respaldado")]


def _check_evidence_claim_ids(ctx: CheckContext) -> list[GateResult]:
    memo = ctx.research.artifact_path("decision-memo.md")
    if not memo.exists():
        return [GateResult("evidence_claim_ids", False, "falta decision-memo.md")]
    frontmatter = parse_artifact(memo).frontmatter
    if "evidence_claim_ids" not in frontmatter and ctx.research.meta.schema_version < 2:
        return [
            GateResult(
                "evidence_claim_ids",
                True,
                "legacy schema v1 decision lineage unavailable",
            )
        ]
    value = frontmatter.get("evidence_claim_ids")
    try:
        claim_ids = schema.validate_evidence_claim_ids(value)
    except ValueError as exc:
        return [GateResult("evidence_claim_ids", False, f"decision-memo.md: {exc}")]

    try:
        ledger = load_ledger(ctx.research.artifact_path("notes/sources/verification.yaml"))
    except LedgerValidationError as exc:
        return [GateResult("evidence_claim_ids", False, str(exc))]
    ledger_issues = validate_claim_references(ledger, claim_ids)
    if ledger_issues:
        return [
            GateResult(
                "evidence_claim_ids",
                False,
                "invalid verification ledger: " + "; ".join(ledger_issues),
            )
        ]
    claims = {str(claim.get("claim_id")): claim for claim in ledger["claims"]}
    for claim_id in claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            return [
                GateResult(
                    "evidence_claim_ids",
                    False,
                    f"decision-memo.md: claim ID does not exist: {claim_id}",
                )
            ]
        state = str(claim.get("state") or "")
        if state != "verified":
            return [
                GateResult(
                    "evidence_claim_ids",
                    False,
                    "decision-memo.md: claim ID is not currently verified: "
                    f"{claim_id} ({state or 'missing state'})",
                )
            ]
    return [
        GateResult(
            "evidence_claim_ids",
            True,
            f"{len(claim_ids)} claim dependencies verified",
        )
    ]


def _check_asset_metadata(ctx: CheckContext) -> list[GateResult]:
    directory = ctx.research.artifact_path("assets")
    files = sorted(directory.glob("*.md")) if directory.is_dir() else []
    if not files:
        return [GateResult("asset_metadata", False, "assets/ no contiene ningún asset")]
    results: list[GateResult] = []
    for path in files:
        fm = parse_artifact(path).frontmatter
        asset_type = fm.get("type")
        audience = fm.get("audience")
        if not asset_type:
            results.append(GateResult("asset_metadata", False, f"{path.name}: falta 'type'"))
        elif asset_type not in schema.ASSET_TYPES:
            results.append(
                GateResult(
                    "asset_metadata",
                    False,
                    f"{path.name}: type inválido {asset_type!r}; use uno de {schema.ASSET_TYPES}",
                )
            )
        if not audience:
            results.append(GateResult("asset_metadata", False, f"{path.name}: falta 'audience'"))
        elif audience not in schema.ASSET_AUDIENCES:
            results.append(
                GateResult(
                    "asset_metadata",
                    False,
                    f"{path.name}: audience inválido {audience!r}; "
                    f"use uno de {schema.ASSET_AUDIENCES}",
                )
            )
    if not results:
        results.append(GateResult("asset_metadata", True, f"{len(files)} assets"))
    return results


_CHECKS: dict[str, Callable[[CheckContext], list[GateResult]]] = {
    "min_evaluation_criteria": _check_min_evaluation_criteria,
    "source_tiers": _check_source_tiers,
    "source_dates": _check_source_dates,
    "source_triangulation": _check_source_triangulation,
    "links_resolve": _check_links_resolve,
    "tier_plausibility": _check_tier_plausibility,
    "claim_citation_coverage": _check_claim_citation_coverage,
    "criteria_cross_reference": _check_criteria_cross_reference,
    "benchmark_reproducible": _check_benchmark_reproducible,
    "probe_artifacts_exist": _check_probe_artifacts_exist,
    "y_statement": _check_y_statement,
    "ring_backed_by_evidence": _check_ring_backed_by_evidence,
    "evidence_claim_ids": _check_evidence_claim_ids,
    "asset_metadata": _check_asset_metadata,
}


def _default_url_checker(url: str) -> bool:
    import httpx

    try:
        response = fetch_http(url, method="HEAD")
        if response.status_code >= 400:
            response = fetch_http(url)
        return response.status_code < 400
    except (httpx.HTTPError, NetworkPolicyError):
        return False


def check_stage(
    research: Research,
    stage: str | None = None,
    offline: bool = False,
    url_checker: Callable[[str], bool] | None = None,
) -> GateReport:
    """Ejecuta las capas estructural y evidencial de la etapa indicada."""
    stage = stage or research.meta.stage
    spec = schema.artifact_for(stage, schema_version=research.meta.schema_version)
    report = GateReport(stage=stage)
    if spec.collection_dir:
        report.results.extend(_check_collection(research, spec))
    else:
        report.results.extend(_check_single_file(research, spec))
    if any(result.check == "duplicate_frontmatter_key" for result in report.results):
        return report
    ctx = CheckContext(
        research=research,
        stage=stage,
        offline=offline,
        url_checker=url_checker or _default_url_checker,
    )
    for check_name in spec.checks:
        report.results.extend(_CHECKS[check_name](ctx))
    return report
