# repo-audit v0.2.1 — SpecGen

## Overview

Adds `repo-audit specgen` to `bread-forge/repo-audit`. SpecGen groups related findings
(by module overlap) into coherent kiln spec files — one spec per cluster of related gaps,
not one spec per finding. Each generated spec has `## Overview`, `## Goals`, `## Modules`,
a stub `## Validation` section, and `## Meta` YAML front-matter with `source_findings`,
`blast_radius`, `priority`, and `depends_on`. Output is written to a target `specs/`
directory for human review before being given to kiln.

## Goals

- **[P0]** `repo-audit specgen <repo-path> --out-dir <specs-dir>` generates kiln spec
  files from the most recent finding cycle
- **[P0]** Clustering: two findings cluster together if their `blast_radius.modules_affected`
  share ≥1 module; max cluster size = 5 findings per spec; deterministic (sorted by finding id)
- **[P0]** Each spec has valid kiln format: `## Overview`, `## Goals`, `## Modules`,
  `## Validation` (stub shell commands), `## Meta` front-matter
- **[P0]** `## Meta` front-matter includes: `source_findings` (list of finding ids),
  `blast_radius` (union of constituent findings), `priority` (highest severity in cluster),
  `depends_on` (empty list — PE fills this in later)
- **[P1]** Deduplication: reads existing `specs/*.md` in out-dir; skips generating specs
  for finding clusters already covered (matches on `source_findings` overlap)
- **[P1]** >60% of generated specs approvable by a human without modification (heuristic
  target; measure by running SpecGen against kiln and reviewing output)
- **[P1]** `--dry-run` flag: prints cluster plan without writing files
- **[P2]** `repo-audit specgen --since <cycle-id>` only generates specs for new findings
- All tests pass; new tests cover clustering algorithm, spec formatter, dedup

## Modules

- `specgen/clusterer`: module-overlap graph clustering; deterministic sort
- `specgen/formatter`: spec file generator; `## Meta` front-matter writer
- `specgen/dedup`: reads existing specs dir; computes source_findings overlap
- `cli`: `repo-audit specgen` command with `--out-dir`, `--dry-run`, `--since` flags

## Validation

```bash


# SpecGen produces at least 1 spec from kiln findings
uv run repo-audit specgen ../kiln --out-dir /tmp/generated-specs --dry-run
uv run repo-audit specgen ../kiln --out-dir /tmp/generated-specs
ls /tmp/generated-specs/*.md | wc -l

# Each spec has required sections and meta front-matter
python -c "
from pathlib import Path
import yaml

specs = list(Path('/tmp/generated-specs').glob('*.md'))
assert specs, 'No specs generated'
for p in specs:
    text = p.read_text()
    assert '## Overview' in text, p
    assert '## Goals' in text, p
    assert '## Validation' in text, p
    # Meta front-matter: extract YAML between --- markers
    if text.startswith('---'):
        meta_text = text.split('---')[1]
        meta = yaml.safe_load(meta_text)
        assert 'source_findings' in meta, p
        assert 'priority' in meta, p
print(f'All {len(specs)} specs valid')
"

# Dedup skips already-covered findings on second run
uv run repo-audit specgen ../kiln --out-dir /tmp/generated-specs
# Second run should produce 0 new specs (all already exist)

# All tests pass
uv run python -m pytest tests/ -q
```
