---
research: <slug>
date: <YYYY-MM-DD>
stage: explore
sources:
  - id: S1
    url: https://<official-source>
    tier: T1        # T1 official doc/paper/code, T2 benchmark/blog, T3 opinion
    # tier_justification: <if the declared tier is stronger than the derived one>
    date: <YYYY-MM-DD>
    # date_justification: <if the source is older than the expected freshness>
    alternative: <alternative-name>   # optional; groups the T1 requirement per alternative
  - id: S2
    url: https://<second-source>
    tier: T2
    date: <YYYY-MM-DD>
---

## Alternatives evaluated

<!-- Alternatives in the solution space and how they compare.
     Use [S1] only for a near-literal factual statement from the S1 snapshot: it creates
     a claim for `sdr verify-claims`. Use [cf. S1] for context, synthesis, or
     interpretation: it keeps traceability but stays out of textual matching.
     Each sentence admits at most one factual reference [Sn]. -->

## Maturity

<!-- Project state: version, community, support, API stability.
     Separate near-literal facts [Sn] from context or synthesis [cf. Sn]. -->

## Costs

<!-- Cost of adoption and operation: licences, compute, integration time. -->

## Risks

<!-- Technical and adoption risks found during exploration. -->

## Counter-evidence

<!-- Findings that contradict or weaken the hypothesis. If you found none, state what
     you searched for and where. -->
