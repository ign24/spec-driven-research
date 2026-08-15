---
research: synthetic-full
date: 2026-07-01
stage: explore
sources:
  - id: S1
    url: https://docs.labels.example/normalization
    tier: T1
    date: 2026-06-15
  - id: S2
    url: https://bench.methods.test/alternatives
    tier: T2
    date: 2026-06-20
---

## Alternatives evaluated
The exercise compares a local function against a fixed lookup table [cf. S1].

## Maturity
Both alternatives are considered sufficient for a training probe [cf. S1].

## Costs
The invented evaluation considers only local execution with the standard library [cf. S2].

## Risks
The fixed set may accidentally favour one alternative [cf. S2].

## Counter-evidence
The possibility that the result does not generalize is kept explicit [cf. S2].
