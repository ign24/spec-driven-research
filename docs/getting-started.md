# Complete a Light Investigation Offline

[Español](getting-started.es.md)

This beginner path uses only the maintained
[synthetic light fixture](../examples/light-complete/README.md). Every name,
source, and result is invented. Commands run offline, no probe is required or
executed, approval is explicit, and reuse is mandatory.

The lifecycle is `intake -> explore -> transfer -> reuse -> done`.

## Before you start

Run from a source checkout with Python 3.12+, `uv`, and Git available:

```bash
uv sync --locked --all-extras --dev
export SDR_ROOT="$(mktemp -d)/research"
```

`SDR_ROOT` keeps the generated investigation outside the checkout. The fixture
files remain unchanged.

## 1. Create intake

```bash
uv run sdr new synthetic-light \
  --title "Synthetic evaluation of a rule-based classifier" \
  --question "Is it worth evaluating a deterministic classifier to sort fictional cards?" \
  --mode light \
  --owner "Example Researcher" \
  --timebox 2 \
  --no-commit
cp examples/light-complete/brief.md "$SDR_ROOT/synthetic-light/brief.md"
uv run sdr check synthetic-light --offline --json
uv run sdr advance synthetic-light --offline --no-commit
```

The checked [brief](../examples/light-complete/brief.md) defines two evaluation
criteria. A successful advance enters `explore`.

## 2. Add exploration evidence

```bash
cp -R examples/light-complete/notes/. "$SDR_ROOT/synthetic-light/notes/"
uv run sdr check synthetic-light --offline --json
uv run sdr advance synthetic-light --offline --no-commit
```

The [landscape note](../examples/light-complete/notes/landscape.md) uses only
reserved synthetic domains and contextual citations. Offline link checking is
reported as skipped, not passed. A successful advance enters `transfer`; light
mode does not create or execute a probe.

## 3. Approve the decision explicitly

```bash
cp examples/light-complete/decision-memo.md "$SDR_ROOT/synthetic-light/decision-memo.md"
uv run sdr check synthetic-light --offline --json
uv run sdr approve synthetic-light --by "Example Reviewer" --offline
uv run sdr advance synthetic-light --offline --no-commit
```

The [decision memo](../examples/light-complete/decision-memo.md) limits its
recommendation to synthetic evidence. `approve` records a named human decision;
it is not implied by `check` and cannot be replaced by claim resolution. The
advance then enters `reuse`.

## 4. Complete mandatory reuse

```bash
cp examples/light-complete/assets/checklist.md "$SDR_ROOT/synthetic-light/assets/checklist.md"
uv run sdr check synthetic-light --offline --json
uv run sdr advance synthetic-light --offline --no-commit
uv run sdr status synthetic-light --json
```

The reusable [checklist](../examples/light-complete/assets/checklist.md) is
required even in light mode. The final status JSON reports `"stage": "reuse"`
and `"status": "done"`; it also records all four validation stages and the
approval by `Example Reviewer`.

## Git and file effects

| Command used here | Files or state | Network in this guide | Git commit |
| --- | --- | --- | --- |
| `sdr new ... --no-commit` | Creates `sdr.yaml`, directories, and the brief template. | None | No; `new` commits by default without `--no-commit`. |
| `cp` | Replaces or adds generated research artifacts from the fixture. | None | No. |
| `sdr check ... --offline` | Evaluates the current stage; offline prevents link checks and snapshot capture. | None | No. |
| `sdr approve ... --offline` | Writes transfer approval metadata. | None | No; `approve` has no commit side effect. |
| `sdr advance ... --offline --no-commit` | Stores validation hashes and changes lifecycle state. | None | No; `advance` commits by default without `--no-commit`. |
| `sdr status ... --json` | Reads current state and an offline gate view. | None | No. |

Across SDR, `new`, `advance`, `reopen`, `drop`, and `archive` commit by default.
Pass `--no-commit` whenever another tool or person owns Git history. See the
[CLI reference](cli-reference.md#git-behavior) for the complete side-effect
contract and the [workflow guide](workflow.md) before using real investigations.
