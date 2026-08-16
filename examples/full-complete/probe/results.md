---
research: synthetic-full
date: 2026-07-01
stage: probe
verify:
  action: run
  argv: [python, check.py]
  expect: SYNTHETIC_PROBE_OK
  environment: clean
---

## Results by criterion
| criterion | result | evidence |
|---|---|---|
| C1 | meets: the three outputs match | `probe/check.py` |
| C2 | meets: two evaluations match | `probe/check.py` |

## Reproduction
```bash
python check.py
```
