"""ReadmeHandler — generates a project README after all build/merge nodes complete.

Uses run_agent with Read/Write/Bash tools to write README.md and open a PR.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from beads.types import GraphNode

from kiln.agents.prompts import readme_agent_prompt
from kiln.agents.runner import run_agent
from kiln.graph.node import NodeResult

if TYPE_CHECKING:
    from beads.store import BeadStore

    from kiln.config import Config
    from kiln.logger import Logger




class ReadmeHandler:
    """Generates a README.md for the repo and opens a PR."""

    def __init__(
        self,
        store: BeadStore | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._store = store
        self._logger = logger

    async def execute(self, node: GraphNode, config: Config) -> NodeResult:
        repo = config.repo
        milestone = node.context.get("milestone", "")
        plan_artifact = self._load_plan_artifact(node)

        prompt = readme_agent_prompt(repo, milestone, plan_artifact)
        workspace = Path(tempfile.mkdtemp(prefix=f"kiln-readme-{milestone}-"))

        result = await run_agent(
            prompt,
            model=config.model,
            timeout_minutes=15,
            cwd=workspace,
            allowed_tools=["Bash", "Read", "Write", "Glob"],
        )

        if not result.success:
            return NodeResult(
                success=False,
                error=f"readme agent exit {result.exit_code}: {(result.stderr or '')[:200]}",
            )

        # Close the milestone driver issue with a single summary comment
        milestone_issue_number: int | None = node.context.get("milestone_issue_number")
        if milestone_issue_number:
            modules = plan_artifact.get("modules", [])
            files_per_module = plan_artifact.get("files_per_module", {})
            module_lines = "\n".join(
                f"- `{m}`: {', '.join(f'`{f}`' for f in files_per_module.get(m, []))}"
                for m in modules
            )
            summary = f"**`{milestone}` complete.** All modules built and merged.\n\n{module_lines}"
            subprocess.run(
                [
                    "gh",
                    "issue",
                    "close",
                    str(milestone_issue_number),
                    "--repo",
                    repo,
                    "--comment",
                    summary,
                ],
                capture_output=True,
                text=True,
            )
            if self._store:
                bead = self._store.read_work_bead(milestone_issue_number)
                if bead:
                    bead.state = "closed"  # type: ignore[assignment]
                    self._store.write_work_bead(bead)

        return NodeResult(success=True, output={"readme": True, "repo": repo})

    def _load_plan_artifact(self, node: GraphNode) -> dict:
        """Load plan artifact from the plan node's bead output."""
        plan_node_id = node.context.get("plan_node_id")
        if plan_node_id and self._store:
            try:
                plan_node = self._store.read_node(plan_node_id)
                if plan_node and plan_node.output:
                    return plan_node.output.get("artifact", {})
            except Exception:
                pass
        return {}

    def recover(self, node: GraphNode, config: Config) -> NodeResult | None:
        """Readme nodes have no recoverable state — always re-dispatch."""
        return None
