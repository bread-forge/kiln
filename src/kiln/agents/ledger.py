"""CostLedger — append-only JSONL cost tracking for graph executor runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class CostLedger:
    """Append-only JSONL ledger stored at ~/.kiln/runs/{run_id}.jsonl.

    Each record captures per-node cost so ``kiln cost`` can aggregate
    without depending on external services.
    """

    def __init__(self, runs_dir: Path | None = None) -> None:
        self._runs_dir = runs_dir if runs_dir is not None else Path.home() / ".kiln" / "runs"

    def _path(self, run_id: str) -> Path:
        return self._runs_dir / f"{run_id}.jsonl"

    def append(
        self,
        run_id: str,
        node_id: str,
        node_type: str,
        model: str,
        cost_usd: float | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Append one cost record to the run's JSONL file."""
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "node_id": node_id,
            "node_type": node_type,
            "model": model,
            "cost_usd": cost_usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        path = self._path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def total_cost(self, run_id: str) -> float:
        """Sum cost_usd across all records for run_id."""
        path = self._path(run_id)
        if not path.exists():
            return 0.0
        total = 0.0
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    cost = record.get("cost_usd")
                    if cost is not None:
                        total += float(cost)
                except (json.JSONDecodeError, ValueError):
                    continue
        return total

    def summarize(self, run_id: str) -> dict[str, Any]:
        """Return total_cost_usd, node_count, and per_node list for run_id."""
        path = self._path(run_id)
        if not path.exists():
            return {"total_cost_usd": 0.0, "node_count": 0, "per_node": []}
        records: list[dict[str, Any]] = []
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        total = sum(float(r.get("cost_usd") or 0) for r in records)
        return {
            "total_cost_usd": total,
            "node_count": len(records),
            "per_node": records,
        }

    def all_runs(self) -> list[str]:
        """Return all run_ids that have ledger files."""
        if not self._runs_dir.exists():
            return []
        return [p.stem for p in self._runs_dir.glob("*.jsonl")]
