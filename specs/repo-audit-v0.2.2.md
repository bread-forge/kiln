# repo-audit v0.2.2 — Security Scan

## Overview

Adds `repo-audit security-scan` to `bread-forge/repo-audit`. Two-layer: code tools first
(Trivy for CVEs, Gitleaks for secrets), then LLM threat modeling against the declared
architecture. Tool outputs are normalized into `FindingBead` records with
`agent: security-scan-tools` or `agent: security-scan-llm`. Gracefully skips if tools
are not installed.

## Goals

- **[P0]** `repo-audit security-scan <repo-path>` runs Trivy (`trivy fs .`) and Gitleaks
  (`gitleaks detect --source .`) as subprocesses; parses their JSON/JSONL output
- **[P0]** Tool findings normalized to `FindingBead` with `staleness_class: dependency`
  for CVEs and `staleness_class: critical` for detected secrets
- **[P0]** If tools not installed: warns and skips that tool; does not fail the run
- **[P1]** `--llm-threat-model` flag: passes architecture context (README + CLAUDE.md +
  entry points) to LLM for threat modeling; produces `agent: security-scan-llm` findings
- **[P1]** CVE findings include: CVE id, severity, affected package, fixed version
- **[P1]** Secret findings: file path + line number only (no secret value in finding)
- **[P2]** `repo-audit security-scan` integrated into `repo-audit run --security`
- All tests pass; new tests cover subprocess parsing with mock tool output

## Constraints

- Never store secret values in FindingBead — only location (file:line)
- Trivy and Gitleaks invoked as subprocesses, not imported as libraries
- LLM threat modeling requires ANTHROPIC_API_KEY; skips without it

## Modules

- `security/trivy`: Trivy subprocess wrapper; CVE finding normalizer
- `security/gitleaks`: Gitleaks subprocess wrapper; secret finding normalizer
- `security/threat_model`: LLM threat modeling agent
- `cli`: `repo-audit security-scan` command; `--llm-threat-model` flag

## Validation

```bash
cd bread-forge/repo-audit

# Security scan runs (tools may not be installed — graceful skip)
uv run repo-audit security-scan ../kiln 2>&1
echo "Exit: $?"  # must be 0 even if tools absent

# With mock tool output, findings normalize correctly
uv run python -c "
from repo_audit.security.trivy import TrivyScanner
scanner = TrivyScanner()
# Test with synthetic trivy JSON output
mock_output = '{\"Results\": [{\"Vulnerabilities\": [{\"VulnerabilityID\": \"CVE-2024-0001\", \"Severity\": \"HIGH\", \"PkgName\": \"requests\", \"FixedVersion\": \"2.32.0\"}]}]}'
findings = scanner.parse_output(mock_output, repo='bread-forge/kiln', cycle_id='c1')
assert len(findings) == 1
assert findings[0].severity == 'high'
assert 'CVE-2024-0001' in findings[0].evidence_chain[0]
print('Trivy parsing OK')
"

# Secret finding hides secret value
uv run python -c "
from repo_audit.security.gitleaks import GitleaksScanner
scanner = GitleaksScanner()
mock_output = '[{\"RuleID\": \"generic-api-key\", \"Secret\": \"sk-abc123\", \"File\": \"config.py\", \"StartLine\": 42}]'
findings = scanner.parse_output(mock_output, repo='bread-forge/kiln', cycle_id='c1')
assert findings[0].severity == 'critical'
# Secret value must not appear in finding
assert 'sk-abc123' not in str(findings[0].model_dump())
print('Secret redaction OK')
"

# All tests pass
uv run python -m pytest tests/ -q
```
