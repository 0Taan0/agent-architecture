# agent-architect — Visual Multi-Agent Architecture Builder

> Build visually. Understand deeply. Own the code.

Local-first development environment for multi-agent systems: an existing code base
becomes a map (read-only, nothing is written), a designed architecture becomes plain
Python you own, every run records its provenance, and drift between `architecture.yaml`
and the code is shown — never silently fixed.

## What is in here (Slice 0 – 2, parts of 3 – 5)

| Area | Where | Status |
|---|---|---|
| Core model, stable ids, YAML round-trip | `core/models` | done, tested |
| `ctx` facade over the AST | `core/inspector/context.py` | done, tested |
| Adapters: `declarations`, `project_local`, `conventions`, `langgraph` (subprocess + AST fallback) | `core/inspector/adapters` | done, tested |
| Merge with priority *confirmed > declared > inferred*, conflicts reported | `core/inspector/merge.py` | done |
| Canonical hashes, project manifest, ownership | `core/ownership` | done, tested |
| Generator: Jinja2 templates, **plan → apply** (Proposal/Applier), git guard | `core/generator` | done, tested |
| Drift: architecture vs code, declaration drift, generated-file hashes, severity table | `core/drift` | done, tested |
| Impact: field-granular usages + past runs by `architecture_hash` | `core/impact` | done, tested |
| Runner, run manifest (Spec 13.2), JSONL traces (OTel GenAI names), SQLite run store, replay client | `core/runtime`, `core/provenance` | done, tested |
| FastAPI backend (thin, path-traversal guard) | `backend/` | done, tested |
| React Flow read-only canvas, inspector panel, unresolved list, adapter status, drift view, CodeMirror | `frontend/` | done, builds |
| LibCST migrations, evaluation harness, experiments | — | not started (Steps 11+) |

Layer order is enforced by `import-linter` (`.importlinter`): `backend → evaluation →
impact → drift → runtime → generator → inspector → ownership → vcs → models`.

## Quick start

```bash
pip install -e ".[dev]"
pytest                                  # 18 tests, incl. full generate→run→drift circle
lint-imports                            # layer contract

# read-only map of an example project (nothing is written)
python -m backend.cli inspect examples/prozess-mas
python -m backend.cli inspect examples/langgraph-demo      # needs langgraph installed for confirmed topology

# GUI
python -m backend.cli serve             # http://127.0.0.1:8000  (OpenAPI at /docs)
cd frontend && npm install && npm run dev   # http://localhost:5173, proxies /api
```

Then paste a project path (e.g. the absolute path of `examples/prozess-mas`) in the left rail.

### Design → code → run

```bash
# 1. write architecture.yaml (by hand, from the GUI later, or import from code)
python -m backend.cli inspect my-project --save
# 2. preview: every operation with old/new content and a diff, nothing written
python -m backend.cli plan my-project
# 3. write (refuses on a dirty git tree unless --allow-dirty)
python -m backend.cli apply my-project
# 4. implement agents/<name>.py, then run (manifest + trace + runs.db under .architecture/)
python -m backend.cli run my-project --input '{"text": "hello"}'
# 5. compare architecture.yaml against the code
python -m backend.cli drift my-project
```

## How the inspector reads a project

| Adapter | Reads | Provenance | Executes code |
|---|---|---|---|
| `declarations` | `@agent(...)` decorator / `__architecture__` attribute (AST) | declared | no |
| `project_local` | your `.architecture/inspect.py` `discover(ctx)` (runs on the AST via `ctx`) | declared | no (project code is never imported) |
| `conventions` | `agents/`, `*Agent` classes with `run()` | inferred | no |
| `langgraph` | `get_graph()` in a subprocess of the project's venv + AST pass | confirmed (AST fallback: inferred) | yes, subprocess only |

Rules that hold everywhere:

* **Incomplete yes, wrong no.** What cannot be read statically lands in `unresolved`, never disappears.
* **Every element carries `provenance` and `source_adapter`.** Merge priority is fixed; contradictions are reported as `adapter_conflict`, not resolved.
* **Stable ids** derive from file + symbol; two inspections of the same code give the same ids. The confirmed LangGraph path and the AST fallback agree on ids.
* **The decorator is eight lines the user copies** (`examples/snippets/architecture_decl.py`), a runtime no-op, no dependency on this project.

The expected maps in `examples/expected/*.yaml` are the acceptance criterion of the inspector (Step 0 of the build guide); the tests compare against them.

## Ownership contract (Spec ch. 7)

```
agents/_generated/<name>_base.py   generated  — regenerated only when its manifest hash still matches
agents/<name>.py                   user       — created once, never touched again
orchestration/_generated/*         generated
models/_generated/schemas.py       generated  (plain dataclasses, no framework)
main.py                            user
.architecture/manifest.yaml        ownership + content hashes + generator/template versions
.architecture/runs.db, traces/     runs and JSONL traces
architecture.yaml                  the architecture, versioned with your code
```

A generated file that was edited by hand is a **conflict** in the plan, not an overwrite.
Existing files unknown to the manifest are treated as user-owned.

## Repository layout

```
core/        pure Python (no IO in models, no framework)
backend/     FastAPI, routes call into core only; project registry lives in ~/.agent-architect
frontend/    Vite + React + TypeScript + @xyflow/react + dagre + CodeMirror (read-only)
examples/    prozess-mas (own architecture), decorated (@agent), langgraph-demo, expected/ maps
tests/
```

Frontend types: `npm run gen:types` regenerates `src/api-types.d.ts` from the backend's
OpenAPI schema; `src/types.ts` is the hand-picked subset the UI uses.

## Open decisions (Spec ch. 16) as implemented

* Architecture in the repo (`architecture.yaml`), runs in SQLite inside the project.
* Plain Python + thin orchestrator; framework templates can be added as a template set.
* Orchestrator is implicit (sequential, derived from connections). Conditional edges are flattened for now.
* `config/agents.yaml` does not exist — `architecture.yaml` is the only source of truth.
* Tools are strings in the node model; a real tool definition is a later slice.
