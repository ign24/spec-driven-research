# Releasing

## Current policy

CI builds and audits wheel and source distributions, then retains them only as a
short-lived GitHub Actions artifact.

The only supported installation route is a `uv tool install` from the canonical
repository at an explicit revision, documented in the [root README](../README.md#install-from-source).
Documentation must not present any other acquisition route as available, and
must not present one as a scheduled or forthcoming route either.

Pull-request workflows stay secretless and read-only. No publication credential,
placeholder credential, or publish workflow is stored in this repository.
