## Context

SDR is distributed only from its public Git repository. `pyproject.toml` declares no `[project.urls]`,
so the repository coordinate lives exclusively in prose across `README.md` and `README.es.md`. That
prose drifted: twelve references named `ign24/sdr` while the repository is `ign24/spec-driven-research`,
and the documented install command worked only because GitHub answers the former with a `301`. A future
repository under the old name would silently repoint the documented install command at unrelated code.

The three supported adapters ship canonical skills whose front matter already describes when each
individual stage skill applies. What is missing is the prior question: whether an investigation belongs
in SDR at all. Nothing in the repository states the conditions under which a host agent should reach
for SDR, so installation alone does not produce adoption.

`tests/test_packaging.py` inspects built artifact contents, and `sdr.artifact_audit` inspects wheel and
sdist members, but no test installs SDR the way a user does and runs it. The
`release-hardening-and-public-distribution` change specified that harness in its section 5, coupled to
a package-index release matrix that is now withdrawn.

## Goals / Non-Goals

**Goals:**

- Prove, by execution, that the documented git install command produces a working `sdr` console script.
- Give the repository coordinate one machine-checkable source of truth.
- State the SDR routing contract once, in a form a user can paste into a host agent's instructions.

**Non-Goals:**

- Publishing to PyPI or any package index, and every artifact that exists only to serve publication:
  release manifests, Trusted Publishing, tag-triggered candidate flows, and release rehearsal.
- Claiming platform support beyond the validated Linux matrix.
- Host end-to-end evidence. Adapter status stays `documented`; a routing block is documentation, not
  proof that a host followed it.

## Decisions

### Decision 1: `[project.urls]` is the canonical coordinate, documentation derives from it

The repository coordinate goes in `pyproject.toml` under `[project.urls]`, and tests assert that every
documented repository URL matches it. The alternative — a constant in `src/sdr/` — was rejected because
the coordinate is packaging metadata that belongs in distribution metadata, and because
`src/sdr/__init__.py` is already the single version source and should not accumulate a second identity
role.

This mirrors the fix already applied to GitHub Actions pinning: the test derives expectations from the
authoritative artifact instead of storing a second copy that must be updated by hand.

### Decision 2: Redirect resolution is a finding, not a pass

A documented URL that reaches the canonical repository only through a `301` is reported. Accepting
redirects would have accepted exactly the drift that motivated this change, because every stale URL in
the READMEs resolved successfully. The check compares the documented coordinate against the declared
one directly and does not consult the network, so it stays deterministic and offline.

### Decision 3: Installation verification pins a revision and runs a real lifecycle

The harness installs from an explicit revision — not the default branch — into a temporary environment
outside the checkout, with the source tree absent from `PYTHONPATH`, then drives a complete light
lifecycle through the installed console script. Asserting only that `sdr --help` exits zero would not
detect missing packaged resources, which is the failure mode that separates a working install from an
importable one.

The revision under test is the current `HEAD` commit, so the harness verifies the tree being tested
rather than whatever the remote branch holds. It requires network access and is marked so it can be
deselected offline, with an explicit skip reason rather than silent absence.

### Decision 4: The routing block is one canonical text, published per adapter

The routing block is authored once as a package resource and reproduced in each adapter guide, with
parity tests binding the copies to the source. Authoring it three times invites the same divergence
that produced the URL drift. The block states both when SDR applies and when it does not; a routing
instruction that only ever says "yes" produces investigations for questions that did not need one.

### Decision 5: The withdrawn change is superseded explicitly, not deleted silently

`release-hardening-and-public-distribution` is withdrawn with a recorded reason, retaining its history.
Its installation-relevant scope moves here. Deleting the directory would erase the record of why a
package-index release was specified and then abandoned.

## Risks / Trade-offs

- **A network-dependent test is the only proof the install path works.** → Mark it so CI runs it and
  local offline runs deselect it with an explicit reason. A silently skipped install test is
  indistinguishable from no test, so absence must be reported.
- **Installing from a pinned revision does not prove the default branch installs.** → The pinned
  revision is `HEAD`, which is the commit CI is testing; the default branch is that commit once merged.
- **A routing block cannot make a host agent comply.** → Keep adapter status at `documented` and state
  in the block itself that it is guidance the user installs, not enforcement.
- **Withdrawing package-index work leaves SDR uninstallable by `pip install spec-driven-research`.** →
  Accepted and stated: the git revision install is the supported route, and documentation must not
  imply an index route exists.

## Open Questions

- Whether the routing block should also ship as an installable artifact through
  `sdr integrations install`, or remain copy-paste guidance. Deferred until the block's text settles.
