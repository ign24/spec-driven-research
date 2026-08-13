# Maintenance and Validation

This public document is the maintenance checklist for framework contributors.
Tests and automation should link here.

## Validation controls

Runtime documentation uses this conceptual order: **Structural**,
**Evidential**, **Textual anchoring**, **Executable**, **Hash consistency**, and
**HITL**. The implementation checks consistency before current-stage controls.
The Context Graph is auxiliary and non-blocking, not complete lineage.

## Local quality checks

Install all development and optional dependencies:

```bash
uv sync --all-extras --dev
```

Run focused tests during TDD, then the complete checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

GitHub Actions runs this gate on `ubuntu-latest` with Python 3.12 and Python 3.13.
This Ubuntu/Linux matrix is the current compatibility evidence. Windows and
macOS are not currently validated or claimed as supported platforms.

Package validation builds both a wheel and source distribution, verifies that
`src/sdr/__init__.py` supplies the version, runs `twine check`, enforces archive
member allowlists, audits extracted content with `sdr.public_tree_audit`, and
installs each artifact in a separate environment for smoke tests.

## Documentation checks

`tests/test_templates.py` verifies the public documentation inventory,
validation terminology, full/light lifecycle, offline example, contextual
citation semantics, approval separation, trust boundaries, and probe execution
contract. `tests/test_examples_e2e.py` runs the maintained synthetic light and
full fixtures offline and verifies their final states without modifying them.
`sdr.readme_parity` verifies concept coverage across all three bilingual pairs:
the root READMEs, documentation homes, and beginner guides. It also resolves
their local links and fixture references and rejects package-index installation
claims while publication is disabled. Findings are actionable; equivalent
nonliteral translations are accepted.
`tests/test_packaging.py` verifies README, license, author, version, and changelog
metadata.

Add public maintenance guidance here and keep assertions tied to observable
behavior. When any onboarding pair or the synthetic tour changes, preserve the
shared contract in both languages and run the focused gates first:

```bash
uv run pytest tests/test_readme_parity.py tests/test_templates.py tests/test_examples_e2e.py
uv run python -m sdr.readme_parity .
uv run python examples/runner.py light-complete --root "$(mktemp -d)/research"
```

Then run the broader documentation and repository checks:

```bash
uv run python -m sdr.skill_validation .
uv run python -m sdr.integration_validation validate .
npm install --global @fission-ai/openspec@1.2.0
openspec validate --specs --strict --no-interactive
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
```

## Release checklist

1. Confirm an active OpenSpec change covers user-visible behavior.
2. Ensure new behavior was developed from a failing focused test.
3. Update affected English and Spanish docs plus `CHANGELOG.md`.
4. Confirm `src/sdr/__init__.py` remains the only version source of truth.
5. Run the full test, lint, and format commands above.
6. Build artifacts and run the public-tree audit before publication.
7. Inspect package metadata and wheel contents for private material.
8. Verify integration statuses remain honest; `documented` is not E2E-tested.
9. Follow [Releasing](releasing.md); no PyPI publishing workflow is currently enabled.

## Public content audit

Never publish research directories, knowledge artifacts, notebooks, environment
files, caches, nested repositories, credentials, private paths, organization
names, or real investigation data. Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m sdr.public_tree_audit . \
  --exclude .git --exclude .claude --exclude .venv \
  --exclude .pytest_cache --exclude .ruff_cache \
  --exclude build --exclude dist --exclude examples/__pycache__ \
  --exclude src/sdr/__pycache__ --exclude tests/__pycache__ \
  --exclude bench/__pycache__ --exclude bench/harness/__pycache__
```

Review findings manually. Pattern matching cannot prove that content is safe.
The exclusions are ignored local, cache, and build state outside the candidate
public surface. This command does not inspect or delete excluded state.

## Security checks

The security workflow exports locked runtime requirements and runs `pip-audit`
without ignored advisories. It also uses the official Gitleaks CLI fixed at
`v8.28.0` to scan the working tree, complete Git history, staged patch, and
built archives with `--redact`. The CLI tag resolves to commit
`39fdb480a06768cc41a84ef86959c07ff33091c4`.

The commercial `gitleaks-action` v2 is intentionally not used because
organization repositories require a license secret and its PR-comment behavior
needs broader GitHub API access. The secretless CLI is complemented by the local
public-tree and artifact auditors:

```bash
uv export --locked --no-dev --no-emit-project --no-hashes --output-file /tmp/sdr-runtime.txt
uv run pip-audit --requirement /tmp/sdr-runtime.txt
# Run the exact candidate-surface public-tree command above.
gitleaks dir --redact --no-banner .
gitleaks git --redact --no-banner --log-opts="--all" .
git diff --cached --binary | gitleaks --redact --no-banner stdin
uv build
uv run twine check dist/*
uv run python -m sdr.artifact_audit dist/*
gitleaks dir --redact --no-banner --max-archive-depth=1 dist
```

Pass the candidate wheel and matching sdist to the artifact auditor together.
Their independent content checks are preserved, and the paired checks compare
packaged integration resources and bind the sdist's sanitized canary evidence
to the wheel filename and version.

The canary evidence also records the SHA-256 of the wheel the canaries actually
ran against. Every rebuild from changed sources produces a different digest, so
that field is provenance rather than a routine gate. Add `--release` when
auditing the exact artifacts being promoted; it additionally requires the
recorded digest to match the audited wheel bytes:

```bash
uv run python -m sdr.artifact_audit --release dist/*
```

`--release` therefore requires re-running the documented discovery canaries
against the candidate build before publication.

All external GitHub Actions are pinned to full commit SHAs. The workflow comments
record their release tags; `.github/dependabot.yml` checks GitHub Actions and the
Python/uv lock inputs weekly.
