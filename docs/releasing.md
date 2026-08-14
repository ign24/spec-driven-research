# Releasing

## Current policy

CI builds and audits wheel and source distributions, then retains them only as a
short-lived GitHub Actions artifact. The repository does not publish to PyPI and
does not store or consume a PyPI API token.

The only supported installation route is a `uv tool install` from the canonical
repository at an explicit revision, documented in the [root README](../README.md#install-from-source).

## No package-index route

Publication to a package index is out of scope and not enabled.
PyPI Trusted Publishing through GitHub Actions OIDC is not configured for this
repository, and no OpenSpec change specifies it. Documentation must not present
installation from a package index as available, and must not present it as a
scheduled or forthcoming route either.

Pull-request workflows stay secretless and read-only. No publication token,
placeholder token, or publish workflow is stored in this repository.
