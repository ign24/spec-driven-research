"""Pure expectation-blind prompt templates and treatment separation."""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from bench.harness.actor import ARMS, ActorKind
from bench.harness.enforcement import BoundaryError


class EvaluationQuestion(StrEnum):
    """The three independent questions measured by the harness."""

    LIFECYCLE_CONTROL_OBSERVABILITY = "lifecycle-control-observability"
    LIVE_SINGLE_INVESTIGATION = "live-single-investigation"
    CROSS_RETRIEVAL = "cross-retrieval"


class PromptPolicy(StrEnum):
    """Whether a reuse treatment directs generic cross consultation."""

    ASSISTED = "assisted"
    UNASSISTED = "unassisted"


class HistoryCondition(StrEnum):
    """Whether an isolated reuse scenario contains declared prior investigations."""

    PRESENT = "history-present"
    ABSENT = "history-absent"


class PromptLeakError(ValueError):
    """Raised when declared prompt text exposes evaluation-only information."""


class TreatmentAggregationError(ValueError):
    """Raised when callers attempt to combine incompatible treatments."""


@dataclass(frozen=True)
class PromptSource:
    """Prompt-safe focal source text, always rendered as untrusted input."""

    label: str
    content: str

    def __post_init__(self) -> None:
        _nonempty(self.label, "source label")
        _nonempty(self.content, "source content")


@dataclass(frozen=True)
class PromptInputs:
    """The complete allowlist of values that may enter a rendered prompt."""

    evaluation_question: EvaluationQuestion
    research_question: str
    arm: str
    policy: PromptPolicy | None
    history_condition: HistoryCondition | None
    workflow_instructions: tuple[str, ...] = ()
    focal_sources: tuple[PromptSource, ...] = ()
    stop_at_transfer: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation_question, EvaluationQuestion):
            raise ValueError("evaluation_question must be a declared EvaluationQuestion")
        _nonempty(self.research_question, "research question")
        if self.arm not in ARMS:
            raise ValueError(f"arm must be one of {ARMS}")
        if self.policy is not None and not isinstance(self.policy, PromptPolicy):
            raise ValueError("policy must be assisted, unassisted, or not applicable")
        if self.history_condition is not None and not isinstance(
            self.history_condition, HistoryCondition
        ):
            raise ValueError("history condition must be present, absent, or not applicable")
        if not all(
            isinstance(value, str) and value.strip() for value in self.workflow_instructions
        ):
            raise ValueError("workflow instructions must be non-empty strings")
        if not all(isinstance(source, PromptSource) for source in self.focal_sources):
            raise ValueError("focal sources must contain only PromptSource values")
        if not isinstance(self.stop_at_transfer, bool):
            raise ValueError("stop_at_transfer must be boolean")


@dataclass(frozen=True)
class PromptLeakSignals:
    """Evaluation-only literals checked against a prompt but never interpolated into it."""

    planted_defect_identities: tuple[str, ...] = ()
    expected_detection_values: tuple[str, ...] = ()
    expected_detection_metadata: tuple[str, ...] = ()
    positive_retrieval_expectations: tuple[str, ...] = ()
    negative_control_records: tuple[str, ...] = ()
    hidden_seed_identities: tuple[str, ...] = ()
    hidden_queries: tuple[str, ...] = ()
    hidden_result_hints: tuple[str, ...] = ()
    completed_decision_answers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for category, values in self.categories():
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError(f"{category} leak signals must be non-empty strings")

    def categories(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Signals in deterministic validation order."""
        return (
            ("planted-defect", self.planted_defect_identities),
            ("expected-detection-value", self.expected_detection_values),
            ("expected-detection-metadata", self.expected_detection_metadata),
            ("positive-retrieval-expectation", self.positive_retrieval_expectations),
            ("negative-control-record", self.negative_control_records),
            ("hidden-seed-identity", self.hidden_seed_identities),
            ("hidden-query", self.hidden_queries),
            ("hidden-result-hint", self.hidden_result_hints),
            ("completed-decision-answer", self.completed_decision_answers),
        )


@dataclass(frozen=True)
class PromptTemplate:
    """Immutable template provenance."""

    identifier: str
    version: str
    sha256: str
    body: bytes


@dataclass(frozen=True)
class PromptTreatment:
    """Prompt treatment factors before an actor is selected."""

    evaluation_question: EvaluationQuestion
    arm: str
    policy: PromptPolicy | None
    history_condition: HistoryCondition | None
    template_identifier: str
    template_version: str
    template_sha256: str


@dataclass(frozen=True)
class TreatmentIdentity:
    """Complete grouping identity; no field may be averaged away."""

    actor: ActorKind
    evaluation_question: EvaluationQuestion
    arm: str
    policy: PromptPolicy | None
    history_condition: HistoryCondition | None
    template_identifier: str
    template_version: str
    template_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.actor, ActorKind):
            raise ValueError("treatment actor must be an ActorKind")
        if not isinstance(self.evaluation_question, EvaluationQuestion):
            raise ValueError("treatment evaluation question is invalid")
        if self.arm not in ARMS:
            raise ValueError(f"treatment arm must be one of {ARMS}")
        _nonempty(self.template_identifier, "template identifier")
        _nonempty(self.template_version, "template version")
        if not re.fullmatch(r"[0-9a-f]{64}", self.template_sha256):
            raise ValueError("template sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class BuiltPrompt:
    """A validated prompt and the exact treatment identity used to build it."""

    text: str
    template: PromptTemplate
    treatment: PromptTreatment
    leak_validation_passed: bool
    inputs: PromptInputs
    leaks: PromptLeakSignals


@dataclass(frozen=True)
class LivePromptEvidence:
    """Recomputed template and exact submitted-byte evidence."""

    template_bytes: bytes
    template_sha256: str
    submitted_bytes: bytes
    submitted_sha256: str
    leak_validation_passed: bool


_TEMPLATE_VERSION: Final[str] = "1"
_QUESTION_INSTRUCTIONS: Final[Mapping[EvaluationQuestion, str]] = MappingProxyType(
    {
        EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY: (
            "Carry out the declared research workflow for the focal question. "
            "Treat lifecycle feedback as operational guidance and do not infer fixture intent."
        ),
        EvaluationQuestion.LIVE_SINGLE_INVESTIGATION: (
            "Investigate the focal question within the declared workflow and operator boundaries."
        ),
        EvaluationQuestion.CROSS_RETRIEVAL: (
            "Investigate the focal question using only the declared scenario and "
            "workflow boundaries."
        ),
    }
)
_ARM_INSTRUCTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "baseline": "Use the declared no-lifecycle baseline procedure.",
        "light": "Use the declared light workflow.",
        "full": "Use the declared full workflow.",
    }
)
_SCORING_VOCABULARY: Final[tuple[str, ...]] = (
    "expected_detection",
    "positive_expectations",
    "negative_controls",
    "caught",
    "uncaught",
    "missed",
    "not-exercised",
    "not_exercised",
    "correct",
    "incorrect",
    "not-consulted",
    "not_consulted",
)
_TRANSFER_STOP_INSTRUCTION: Final[str] = (
    "Stop at transfer and return control to the real operator. Do not invoke approval, "
    "substitute claim resolution, or continue past the operator boundary."
)


def build_prompt(inputs: PromptInputs, leaks: PromptLeakSignals) -> BuiltPrompt:
    """Build and leak-check one deterministic prompt without performing any I/O."""
    if not isinstance(inputs, PromptInputs):
        raise TypeError("build_prompt accepts PromptInputs, never whole fixture objects")
    if not isinstance(leaks, PromptLeakSignals):
        raise TypeError("leaks must be PromptLeakSignals")
    _validate_treatment(inputs)

    assisted = (
        "You must consult `sdr cross` during the investigation, selecting commands from the focal "
        "question and observed evidence without relying on undeclared hints."
        if inputs.policy is PromptPolicy.ASSISTED
        else ""
    )
    template_body = "\n".join(
        part
        for part in (
            _QUESTION_INSTRUCTIONS[inputs.evaluation_question],
            _ARM_INSTRUCTIONS[inputs.arm],
            assisted,
            _TRANSFER_STOP_INSTRUCTION if inputs.stop_at_transfer else "",
        )
        if part
    )
    policy_identity = inputs.policy.value if inputs.policy is not None else "standard"
    identifier = f"sdr-bench.{inputs.evaluation_question.value}.{inputs.arm}.{policy_identity}"
    template = PromptTemplate(
        identifier=identifier,
        version=_TEMPLATE_VERSION,
        sha256=hashlib.sha256(template_body.encode("utf-8")).hexdigest(),
        body=template_body.encode("utf-8"),
    )
    policy = inputs.policy.value if inputs.policy is not None else "not-applicable"
    history = (
        inputs.history_condition.value if inputs.history_condition is not None else "not-applicable"
    )
    raw_declared_text = "\n".join(
        (
            inputs.research_question,
            *inputs.workflow_instructions,
            *(value for source in inputs.focal_sources for value in (source.label, source.content)),
        )
    )
    _validate_no_leaks(raw_declared_text, leaks)

    lines = [
        f"template_id: {template.identifier}",
        f"template_version: {template.version}",
        f"template_sha256: {template.sha256}",
        f"evaluation_question: {inputs.evaluation_question.value}",
        f"arm: {inputs.arm}",
        f"policy: {policy}",
        f"history_condition: {history}",
        "",
        template_body,
        "",
        "<declared-research-question>",
        html.escape(inputs.research_question),
        "</declared-research-question>",
    ]
    if inputs.workflow_instructions:
        lines.extend(("", "<neutral-workflow-instructions>"))
        lines.extend(
            f"- {html.escape(instruction)}" for instruction in inputs.workflow_instructions
        )
        lines.append("</neutral-workflow-instructions>")
    for index, source in enumerate(inputs.focal_sources, start=1):
        lines.extend(
            (
                "",
                f'<untrusted-source index="{index}">',
                f"label: {html.escape(source.label)}",
                html.escape(source.content),
                "</untrusted-source>",
            )
        )
    text = "\n".join(lines) + "\n"
    _validate_no_leaks(text, leaks)
    treatment = PromptTreatment(
        evaluation_question=inputs.evaluation_question,
        arm=inputs.arm,
        policy=inputs.policy,
        history_condition=inputs.history_condition,
        template_identifier=template.identifier,
        template_version=template.version,
        template_sha256=template.sha256,
    )
    return BuiltPrompt(
        text,
        template,
        treatment,
        leak_validation_passed=True,
        inputs=inputs,
        leaks=leaks,
    )


def validate_live_prompt(prompt: BuiltPrompt, submitted_bytes: bytes) -> LivePromptEvidence:
    """Prove a host receives the exact immutable prompt that passed canonical validation."""
    if not isinstance(prompt, BuiltPrompt):
        raise TypeError("live execution accepts only canonical BuiltPrompt values")
    if not prompt.leak_validation_passed:
        raise BoundaryError("live prompt did not pass leak validation")
    template_sha256 = hashlib.sha256(prompt.template.body).hexdigest()
    if template_sha256 != prompt.template.sha256:
        raise BoundaryError("live prompt template hash is stale or invalid")
    if build_prompt(prompt.inputs, prompt.leaks) != prompt:
        raise BoundaryError("live prompt is not the canonical build_prompt result")
    expected = prompt.text.encode("utf-8")
    if submitted_bytes != expected:
        raise BoundaryError("submitted prompt bytes differ from validated BuiltPrompt text")
    if "stop at transfer" not in prompt.template.body.decode("utf-8").casefold():
        raise BoundaryError("live prompt template lacks the canonical transfer stop")
    return LivePromptEvidence(
        template_bytes=prompt.template.body,
        template_sha256=template_sha256,
        submitted_bytes=submitted_bytes,
        submitted_sha256=hashlib.sha256(submitted_bytes).hexdigest(),
        leak_validation_passed=True,
    )


def group_treatments(
    identities: Sequence[TreatmentIdentity],
) -> Mapping[TreatmentIdentity, tuple[TreatmentIdentity, ...]]:
    """Group only exact identities, retaining actor and every treatment factor."""
    groups: dict[TreatmentIdentity, list[TreatmentIdentity]] = {}
    for identity in identities:
        if not isinstance(identity, TreatmentIdentity):
            raise TypeError("treatment groups accept only TreatmentIdentity values")
        groups.setdefault(identity, []).append(identity)
    return MappingProxyType({key: tuple(values) for key, values in groups.items()})


def require_compatible_treatment(
    identities: Sequence[TreatmentIdentity],
) -> TreatmentIdentity:
    """Return the shared identity or refuse any cross-treatment aggregation."""
    values = tuple(identities)
    if not values:
        raise TreatmentAggregationError("refusing aggregation of an empty treatment set")
    if not all(isinstance(value, TreatmentIdentity) for value in values):
        raise TypeError("aggregation accepts only TreatmentIdentity values")
    first = values[0]
    if any(value != first for value in values[1:]):
        raise TreatmentAggregationError(
            "refusing aggregation across actor, evaluation question, arm, prompt policy, "
            "history condition, or template identity/version/hash"
        )
    return first


def _validate_treatment(inputs: PromptInputs) -> None:
    reuse = inputs.evaluation_question is EvaluationQuestion.CROSS_RETRIEVAL
    if reuse and (inputs.policy is None or inputs.history_condition is None):
        raise ValueError("cross-retrieval treatment requires policy and history condition")
    if not reuse and (inputs.policy is not None or inputs.history_condition is not None):
        raise ValueError("non-reuse treatment cannot declare prompt policy or history condition")


def _validate_no_leaks(text: str, leaks: PromptLeakSignals) -> None:
    for vocabulary in _SCORING_VOCABULARY:
        if _contains_literal(text, vocabulary):
            raise PromptLeakError(
                f"scoring-vocabulary leak {vocabulary!r}; refusing before host execution"
            )
    for category, values in leaks.categories():
        for value in values:
            if _contains_literal(text, value):
                raise PromptLeakError(f"{category} leak detected; refusing before host execution")


def _contains_literal(text: str, literal: str) -> bool:
    normalized_text = " ".join(html.unescape(text).casefold().split())
    normalized_literal = " ".join(literal.casefold().split())
    if re.fullmatch(r"[a-z0-9_-]+", normalized_literal):
        return (
            re.search(
                rf"(?<![a-z0-9_-]){re.escape(normalized_literal)}(?![a-z0-9_-])",
                normalized_text,
            )
            is not None
        )
    return normalized_literal in normalized_text


def _nonempty(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
