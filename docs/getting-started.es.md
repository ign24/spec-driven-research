# Completa una Investigación Light sin Red

[English](getting-started.md)

Este recorrido inicial usa solamente el
[fixture light sintético mantenido](../examples/light-complete/README.md). Todos
los nombres, fuentes y resultados son inventados. Los comandos funcionan sin
red, no ejecuta probes, la aprobación es explícita y reuse es
obligatorio.

La salida de la herramienta y las secciones de los artefactos están en inglés
(`Question`, `Evaluation criteria`, ...), aunque esta guía esté en español. Si ya
tenías una investigación con secciones en español, corré `sdr migrate` antes de
avanzarla.

El ciclo es `intake -> explore -> transfer -> reuse -> done`.

## Antes de empezar

Ejecuta desde un checkout con Python 3.12+, `uv` y Git disponibles:

```bash
uv sync --locked --all-extras --dev
export SDR_ROOT="$(mktemp -d)/research"
```

`SDR_ROOT` mantiene la investigación generada fuera del checkout. Los archivos
del fixture no cambian.

## 1. Crea intake

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

El [brief](../examples/light-complete/brief.md) validado define dos criterios de
evaluación. Un avance correcto entra en `explore`.

## 2. Agrega evidencia de exploración

```bash
cp -R examples/light-complete/notes/. "$SDR_ROOT/synthetic-light/notes/"
uv run sdr check synthetic-light --offline --json
uv run sdr advance synthetic-light --offline --no-commit
```

La [nota de landscape](../examples/light-complete/notes/landscape.md) usa solo
dominios sintéticos reservados y citas contextuales. El check de enlaces se
reporta como omitido, no aprobado. Un avance correcto entra en `transfer`; el
modo light no crea ni ejecuta un probe.

## 3. Aprueba la decisión de forma explícita

```bash
cp examples/light-complete/decision-memo.md "$SDR_ROOT/synthetic-light/decision-memo.md"
uv run sdr check synthetic-light --offline --json
uv run sdr approve synthetic-light --by "Example Reviewer" --offline
uv run sdr advance synthetic-light --offline --no-commit
```

El [memo de decisión](../examples/light-complete/decision-memo.md) limita su
recomendación a evidencia sintética. `approve` registra una decisión humana con
nombre; `check` no la presupone y la resolución de claims no puede reemplazarla.
El avance entra en `reuse`.

## 4. Completa el reuse obligatorio

```bash
cp examples/light-complete/assets/checklist.md "$SDR_ROOT/synthetic-light/assets/checklist.md"
uv run sdr check synthetic-light --offline --json
uv run sdr advance synthetic-light --offline --no-commit
uv run sdr status synthetic-light --json
```

La [checklist reutilizable](../examples/light-complete/assets/checklist.md) es
obligatoria incluso en modo light. El JSON final muestra `"stage": "reuse"` y
`"status": "done"`; también registra las cuatro etapas validadas y la aprobación
de `Example Reviewer`.

## Efectos sobre Git y archivos

| Comando usado | Archivos o estado | Red en esta guía | Commit Git |
| --- | --- | --- | --- |
| `sdr new ... --no-commit` | Crea `sdr.yaml`, directorios y el template del brief. | Ninguna | No; `new` crea un commit por defecto sin `--no-commit`. |
| `cp` | Reemplaza o agrega artefactos de investigación desde el fixture. | Ninguna | No. |
| `sdr check ... --offline` | Evalúa la etapa; offline impide checks de enlaces y captura de snapshots. | Ninguna | No. |
| `sdr approve ... --offline` | Escribe metadatos de aprobación de transfer. | Ninguna | No; `approve` no crea commits. |
| `sdr advance ... --offline --no-commit` | Guarda hashes de validación y cambia el estado del ciclo. | Ninguna | No; `advance` crea un commit por defecto sin `--no-commit`. |
| `sdr status ... --json` | Lee el estado y una vista offline de los gates. | Ninguna | No. |

En SDR, `new`, `advance`, `reopen`, `drop` y `archive` crean commits por defecto.
Usa `--no-commit` siempre que otra herramienta o persona controle el historial
Git. Consulta la [referencia de la CLI](cli-reference.md#git-behavior) para el
contrato completo de efectos y la [guía del workflow](workflow.md) antes de usar
investigaciones reales.
