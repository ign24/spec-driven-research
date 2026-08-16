"""The retired Spanish section headings, mapped onto the declared English ones.

This module carries Spanish text rather than addressing a user in it: it is the
single declaration of the previous artifact contract, read only by `sdr migrate`
to carry an investigation created under that contract forward. Nothing else in
the product accepts these headings, and no other module repeats them.
"""

from __future__ import annotations

from sdr.schema import (
    SECTION_ADOPTION_RISKS,
    SECTION_ALTERNATIVES,
    SECTION_AUDIENCE,
    SECTION_CONTEXT,
    SECTION_COSTS,
    SECTION_COUNTER_EVIDENCE,
    SECTION_CRITERIA_RESULTS,
    SECTION_EVALUATION_CRITERIA,
    SECTION_HYPOTHESIS,
    SECTION_MATURITY,
    SECTION_NEXT_STEPS,
    SECTION_QUESTION,
    SECTION_RECOMMENDATION,
    SECTION_REPRODUCTION,
    SECTION_RISKS,
    SECTION_RISKS_AND_LIMITS,
    SECTION_SCOPE,
    SECTION_SELECTION_CRITERIA,
)

LEGACY_SECTION_NAMES: dict[str, str] = {
    "Pregunta": SECTION_QUESTION,
    "Hipótesis": SECTION_HYPOTHESIS,
    "Contexto": SECTION_CONTEXT,
    "Alcance": SECTION_SCOPE,
    "Criterios de evaluación": SECTION_EVALUATION_CRITERIA,
    "Riesgos de adopción": SECTION_ADOPTION_RISKS,
    "Alternativas evaluadas": SECTION_ALTERNATIVES,
    "Madurez": SECTION_MATURITY,
    "Costos": SECTION_COSTS,
    "Riesgos": SECTION_RISKS,
    "Contra-evidencia": SECTION_COUNTER_EVIDENCE,
    "Resultados por criterio": SECTION_CRITERIA_RESULTS,
    "Reproducción": SECTION_REPRODUCTION,
    "Recomendación": SECTION_RECOMMENDATION,
    "Criterios de selección": SECTION_SELECTION_CRITERIA,
    "Riesgos y limitaciones": SECTION_RISKS_AND_LIMITS,
    "Próximos pasos": SECTION_NEXT_STEPS,
    "Audiencia": SECTION_AUDIENCE,
}
