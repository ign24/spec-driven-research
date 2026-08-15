---
research: <slug>
date: <YYYY-MM-DD>
stage: probe
verify:
  action: run
  argv: [python, bench.py]
  expect: OK
  environment: clean  # clean | inherit
---

## Results by criterion

<!-- One result per brief criterion, referenced by its ID (C1, C2, ...):
     meets / does not meet / partial, with the evidence that supports it. -->
- C1: <meets|does not meet|partial> - <evidence>
- C2: <meets|does not meet|partial> - <evidence>

## Reproduction

<!-- How to reproduce the test: commands and/or code versioned under probe/.
     Every benchmark table must be accompanied by its command block.
     Run `sdr verify-probe <slug>` and leave it green before advancing. -->

```bash
# commands to reproduce the benchmark/POC
python probe/bench.py
```
