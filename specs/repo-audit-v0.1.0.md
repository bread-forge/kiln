# repo-audit v0.1.0 — Collector + Analyzer + BeadStore Foundation

## Overview

Bootstrap `bread-forge/repo-audit` — the analysis tier (Tier 1) of the ASDLC. This
milestone builds the two foundation components: the collector (harvests intent artifacts
from a repo — README, specs, docs, CLAUDE.md, docstrings, entry points, test structure)
and the analyzer (walks AST, builds import graph, traces reachability from entry points).
Uses `beads` for persistent finding storage. The comparator and verdict layers come in v0.1.1.

## Goals

- **[P0]** `repo-audit collect <repo-path>` harvests: README.md, CLAUDE.md, `specs/*.md`,
  `docs/**/*.md`, module docstrings, `pyproject.toml` entry points; returns
  `CollectedArtifacts` dataclass
- **[P0]** `repo-audit analyze <repo-path>` walks Python ASTs, builds import graph
  (module → set of imported modules), traces reachable symbols from entry points
  declared in `pyproject.toml [project.scripts]` and `__main__.py` files
- **[P0]** Both outputs are serializable to JSON for downstream comparator use
- **[P0]** `BeadStore` used with `beads_dir=~/.repo-audit/beads/` for finding persistence
  (depends on `beads>=0.2.0`)
- **[P1]** `repo-audit run <repo-path>` runs collect + analyze, stores interim artifacts
  at `~/.repo-audit/cache/{repo-slug}/`
- **[P1]** Handles non-Python repos gracefully (skips AST walker, still collects docs)
- All tests pass; unit tests cover collector, AST walker, import graph, reachability

## Constraints

- Python 3.11+, uv-managed; depends on `beads>=0.2.0`
- No LLM calls in this milestone
- Read-only: never modifies target repo
- AST walker uses stdlib `ast` module only; no third-party AST libraries

## Modules

- `collector`: harvests docs, specs, docstrings, entry points into `CollectedArtifacts`
- `analyzer`: AST walker, import graph builder, reachability tracer; returns `AnalysisResult`
- `store`: thin wrapper wiring BeadStore to `~/.repo-audit/beads/`
- `cli`: `repo-audit collect`, `repo-audit analyze`, `repo-audit run` commands (partial)

## Validation

```bash


# Collector harvests kiln artifacts
uv run repo-audit collect ../kiln --output /tmp/artifacts.json
python -c "
import json
a = json.load(open('/tmp/artifacts.json'))
assert 'readme' in a
assert 'specs' in a and len(a['specs']) > 0
assert 'entry_points' in a
print('Collector OK:', list(a.keys()))
"

# Analyzer builds import graph for kiln
uv run repo-audit analyze ../kiln --output /tmp/analysis.json
python -c "
import json
a = json.load(open('/tmp/analysis.json'))
assert 'import_graph' in a
assert 'reachable_from_entry_points' in a
assert len(a['import_graph']) > 5
print(f'Analyzer OK: {len(a[\"import_graph\"])} modules in graph')
"

# Run command caches output
uv run repo-audit run ../kiln
ls ~/.repo-audit/cache/bread-forge-kiln/

# All tests pass
uv run python -m pytest tests/ -q
```
