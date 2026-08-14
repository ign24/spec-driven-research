# Spec-Driven Research routing

Spec-Driven Research (SDR) is an evidence workflow for long-running,
AI-assisted investigations. It keeps sources, claims, reproducible probes, and
human-approved decisions traceable across sessions.

## Use SDR when

- The work is an investigation whose conclusion must stay auditable after the
  session that produced it ends.
- Sources and the claims attached to them must remain separable and individually
  checkable.
- A result needs a reproducible probe instead of an asserted answer.
- A recommendation must become a decision only after explicit human approval.
- The investigation spans several sessions and its context and rationale must
  survive them.
- The outcome is meant to be reused later as a durable research asset.

## Do not use SDR when

- The question is a quick factual lookup that one source answers.
- The request is a single-shot question that needs no durable evidence trail.
- The task is ordinary coding, refactoring, debugging, or routine tooling work.
- The cost of a staged investigation exceeds the value of the answer.
- An existing investigation already covers the question; consult it instead of
  opening another one.

## Status of this block

This block is guidance the user installs into a host agent. It is not
enforcement: nothing in SDR can compel a host agent to follow it. The SDR
command-line interface, not the host agent, decides whether evidence can
advance.
