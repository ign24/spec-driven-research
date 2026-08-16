<p align="center">
  <img src="assets/sdr-banner.png" alt="Spec-Driven Research">
</p>

# Spec-Driven Research

[![CI](https://github.com/ign24/spec-driven-research/actions/workflows/ci.yml/badge.svg)](https://github.com/ign24/spec-driven-research/actions/workflows/ci.yml)
[![Security](https://github.com/ign24/spec-driven-research/actions/workflows/security.yml/badge.svg)](https://github.com/ign24/spec-driven-research/actions/workflows/security.yml)
[![Python CI: 3.12 | 3.13](https://img.shields.io/badge/Python_CI-3.12_%7C_3.13-3776AB?logo=python&logoColor=white)](https://github.com/ign24/spec-driven-research/actions/workflows/ci.yml)
[![Licencia: MIT](https://img.shields.io/badge/Licencia-MIT-yellow.svg)](LICENSE)
[![Estado: Alfa](https://img.shields.io/badge/Estado-Alfa-orange.svg)](CHANGELOG.md)

[English](README.md)

## La IA puede escribir el informe. ¿Podés confiar en la investigación?

**Spec-Driven Research es un workflow de evidencia para investigaciones extensas
asistidas por IA.**

Mantiene fuentes, afirmaciones, pruebas reproducibles y decisiones humanas
trazables entre sesiones, convirtiendo una respuesta convincente en una decisión
revisable y un activo reutilizable:

`pregunta -> evidencia -> probe opcional -> decisión con aprobación humana -> activo reutilizable`

## Por qué existe SDR

Los agentes de investigación son rápidos, pero sus resultados son difíciles de
auditar. Las fuentes pueden desaparecer o no estar disponibles. Una cita puede no
respaldar la afirmación asociada. Los resultados pueden declararse sin una prueba
reproducible. Durante investigaciones extensas, el contexto y las razones se
pierden gradualmente entre sesiones.

SDR da a la investigación una estructura duradera. Preserva la cadena de
evidencia, detecta validaciones obsoletas, separa las afirmaciones respaldadas por
fuentes de las pruebas ejecutables y exige aprobación humana explícita antes de
que una recomendación se convierta en una decisión.

| Investigación habitual con agentes | Spec-Driven Research |
| --- | --- |
| Respuesta final convincente | Cadena de evidencia revisable |
| URLs en una respuesta | Fuentes, metadatos y snapshots |
| Afirmaciones mezcladas con inferencias | Afirmaciones ancladas explícitamente |
| Contexto perdido entre sesiones | Investigación persistente por etapas |
| Resultados declarados en prosa | Probes reproducibles |
| Recomendación generada por un agente | Decisión con aprobación humana |
| Otro informe aislado | Activo de investigación reutilizable |

## Instálalo con tu agente

Pega este mensaje en Claude Code, Codex u OpenCode:

```text
Instala Spec-Driven Research desde
https://github.com/ign24/spec-driven-research en este proyecto. Lee primero su
documentación de instalación e integración con agentes. Explícame qué vas a
cambiar y pide mi aprobación; después instala SDR y las skills locales para
este agente y ejecuta las comprobaciones documentadas. No sobrescribas archivos
existentes ni crees commits.
```

> **Software alfa, disponible solo desde el código fuente.** No hay release en GitHub
> ni en PyPI. Instálalo desde el código fuente canónico en GitHub. Las
> interfaces y los contratos de artefactos pueden cambiar antes del primer release.

## Tour sintético de cinco minutos

Este ejemplo mantenido es inventado, sin red y en modo light. Registra una
aprobación sintética explícita, no ejecuta un probe y completa el reuse obligatorio.

```bash
git clone https://github.com/ign24/spec-driven-research.git
cd spec-driven-research
uv sync --locked --all-extras --dev
TOUR_ROOT="$(mktemp -d)/research"
uv run python examples/runner.py light-complete --root "$TOUR_ROOT"
SDR_ROOT="$TOUR_ROOT" uv run sdr status synthetic-light --json
```

El runner imprime `synthetic-light: done`. El JSON de status muestra `"mode":
"light"`, `"stage": "reuse"`, `"status": "done"` y la aprobación de `Example
Reviewer`. Inspecciona la evidencia materializada en:

- `$TOUR_ROOT/synthetic-light/brief.md`
- `$TOUR_ROOT/synthetic-light/notes/landscape.md`
- `$TOUR_ROOT/synthetic-light/decision-memo.md`
- `$TOUR_ROOT/synthetic-light/assets/checklist.md`

Sigue la [guía inicial](docs/getting-started.es.md) para ejecutar el mismo fixture
con la CLI pública, incluido `sdr approve` explícito y `--no-commit` en cada
transición que podría crear un commit.

## Instalación desde el código fuente

> **La herramienta habla inglés.** Esta guía está en español, pero la interfaz de
> línea de comandos, los mensajes y las plantillas de artefactos están en inglés,
> igual que el resto de la documentación del proyecto. El español es una
> traducción de la documentación, no un idioma de ejecución.
>
> **Cambio incompatible desde la v0.2.0.** Las secciones de los artefactos pasaron
> de nombres en español (`Pregunta`, `Criterios de evaluación`) a nombres en
> inglés (`Question`, `Evaluation criteria`). Una investigación creada con una
> versión anterior requiere `sdr migrate` antes de poder avanzar. La migración
> reescribe solo los encabezados estructurales y deja intacto tu texto.

SDR requiere Python 3.12 o posterior. No hay release en ningún índice de
paquetes: SDR no se publica en PyPI y no se puede instalar desde un índice. La
ruta admitida instala el repositorio canónico en una revisión explícita:

```bash
uv tool install "git+https://github.com/ign24/spec-driven-research@v0.3.0"
sdr --help
```

Fijar la revisión es obligatorio, no opcional. Una instalación sin revisión
sigue la rama por defecto y no es reproducible. Un tag es explícito pero puede
moverse; para una instalación que no pueda cambiar sin aviso, fija el SHA
completo al que apunta el tag:

```bash
REVISION=$(git ls-remote https://github.com/ign24/spec-driven-research v0.3.0 | cut -f1)
uv tool install "git+https://github.com/ign24/spec-driven-research@${REVISION}"
```

Desde un checkout existente, `uv tool install .` ofrece la instalación aislada.
`python -m pip install .` también instala ese checkout. Para contribuir, usa
`uv sync --locked --all-extras --dev`; la extracción de snapshots está en el
extra opcional `snapshot`. Son instalaciones desde fuente, no desde un índice.

## Elige un modo

| Modo | Ciclo | Cuándo usarlo |
| --- | --- | --- |
| `light` | `intake -> explore -> transfer -> reuse -> done` | La comparación basada en fuentes y la revisión humana son suficientes. No requiere probe; reuse sigue siendo obligatorio. |
| `full` | `intake -> explore -> probe -> transfer -> reuse -> done` | La decisión necesita evidencia ejecutable de un probe reproducible. |

Las cinco etapas son `intake`, `explore`, `probe`, `transfer` y `reuse`. Los
artefactos, guards y transiciones se definen en la
[guía del workflow](docs/workflow.md).

## Límites de confianza

SDR valida la estructura declarada y la evidencia local. No demuestra la verdad
de las fuentes ni ofrece garantía de veracidad del material citado. Las personas
siguen siendo responsables de la calidad de las fuentes, la ejecución segura,
la interpretación y la recomendación final.

Los controles siguen este orden conceptual: **Estructural**, **Evidential**,
**Anclaje textual**, **Ejecutable**, **Consistencia de hashes** y **HITL**.
`advance` comprueba la consistencia antes de los controles de la etapa. El
Context Graph opcional es no bloqueante y no representa trazabilidad completa.

- Usa `[S<n>]` para claims factuales destinados al matching local determinístico.
- `[cf. S<n>]` es contextual: no crea un claim ni entra al matching textual. El
  matching no usa modelos.
- `sdr resolve-claim` registra una revisión humana acotada; no reemplaza ni
  sustituye a `sdr approve` en transfer.
- `sdr cross` deriva joins determinísticos, sin modelos y consultivos entre las
  investigaciones almacenadas. Compartir identidad o texto no demuestra la
  identidad del editor, la verdad ni la corroboración independiente, y la capa
  nunca bloquea una transición.
- `sdr check --offline` omite los checks de red y la captura automática de
  snapshots. Los checks omitidos se reportan como omitidos, no aprobados. Por
  ejemplo: `uv run sdr check example-study --offline`.
- Un probe exige `verify.action: run`; prefiere `verify.argv`. SDR ejecuta `argv`
  directamente, sin un shell. Esto no crea un sandbox ni vuelve confiable al
  ejecutable.

Antes de usar fuentes o comandos reales, lee el [modelo de evidencia](docs/evidence-model.md),
la [referencia de validación](docs/validation.md) y el
[modelo de seguridad](docs/security-model.md). Trata Notes, Snapshots,
Repositories, URLs, Probe commands, Git, credenciales y el entorno anfitrión
como límites de confianza.

## Comportamiento de Git

`new`, `advance`, `reopen`, `drop` y `archive` crean commits por defecto.
`acknowledge-degradation` también crea commits por defecto. Usa `--no-commit`
cuando tú, CI o un agente controlen el historial Git. Los comandos de evidencia
o reporte de solo lectura, incluido `sdr cross`, no crean commits. La
[referencia de la CLI](docs/cli-reference.md) es la fuente canónica sobre
mutaciones, red y guards.

## Integraciones con agentes

SDR empaqueta siete skills canónicas de etapa. Exactamente tres adaptadores de
agentes están documentados actualmente:

| Agente | Instalación en el proyecto actual | Estado |
| --- | --- | --- |
| Claude Code | `sdr integrations install --destination .claude/skills` | `documented` |
| Codex | `sdr integrations install --destination .agents/skills` | `documented` |
| OpenCode | `sdr integrations install --destination .opencode/skills` | `documented` |

La forma general es `sdr integrations install --destination PATH_TO_SKILLS`.
El instalador copia recursos del paquete y no usa `SDR_ROOT`, que solo controla
el almacenamiento de investigación. `documented` indica que existen guías de
descubrimiento y checks determinísticos, no un E2E del host. `verified` exige
evidencia E2E registrada y compatible por versión; `experimental` indica un
contrato provisional. Consulta [integraciones](docs/integrations.md).

### Bloque de enrutamiento para agentes

Instalar las skills no le dice al agente anfitrión *cuándo* recurrir a SDR. Esa
pregunta se responde una sola vez, en un bloque de enrutamiento canónico que se
distribuye como recurso del paquete y se publica sin cambios en cada guía de
adaptador. Indica cuándo invocar SDR — una investigación cuya conclusión debe
seguir siendo auditable después de la sesión, cuyas fuentes y afirmaciones deben
poder verificarse por separado, y cuya recomendación se convierte en decisión
solo tras aprobación humana explícita — y cuándo no hacerlo: consultas
factuales rápidas, preguntas de una sola vuelta, trabajo de programación
corriente, o cualquier caso en que el costo de una investigación por etapas
supere el valor de la respuesta.

El bloque es una guía que la persona instala en su agente anfitrión, no un
mecanismo de cumplimiento. Cópialo desde la
[sección del bloque de enrutamiento](docs/integrations.md#agent-routing-block).

## Encuentra la documentación adecuada

Empieza en la [documentación orientada a tareas](docs/README.es.md).

| Objetivo | Guía canónica |
| --- | --- |
| Completar el ciclo mínimo soportado | [Primeros pasos](docs/getting-started.es.md) |
| Entender etapas y retrocesos | [Workflow](docs/workflow.md) |
| Consultar `sdr new`, `sdr check`, `sdr advance`, `sdr status`, `sdr snapshot`, `sdr verify-claims`, `sdr resolve-claim`, `sdr verify-probe`, `sdr approve`, `sdr reopen`, `sdr drop`, `sdr archive`, `sdr index`, `sdr doctor`, `sdr migrate`, `sdr context`, `sdr cross` o `sdr acknowledge-degradation` | [Referencia de la CLI](docs/cli-reference.md) |
| Evaluar claims y límites de evidencia | [Modelo de evidencia](docs/evidence-model.md) |
| Revisar amenazas y límites de confianza | [Modelo de seguridad](docs/security-model.md) y [SECURITY.md](SECURITY.md) |
| Instalar skills para agentes | [Integraciones](docs/integrations.md) |
| Validar una contribución | [Mantenimiento y validación](docs/validation.md) |
| Entender el estado de publicación | [Releasing](docs/releasing.md) |

Las contribuciones siguen [CONTRIBUTING.md](CONTRIBUTING.md) y el trabajo con
agentes también sigue [AGENTS.md](AGENTS.md). SDR usa la [Licencia MIT](LICENSE).
