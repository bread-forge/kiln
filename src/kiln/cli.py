"""kiln CLI."""

from __future__ import annotations

import asyncio
import datetime as _datetime
import json
import os
import subprocess
from datetime import datetime as _dt
from pathlib import Path
from typing import TYPE_CHECKING, Annotated


# Load .env from the current working directory (or parents) at startup,
# before any config or health checks read os.environ.
def _load_dotenv() -> None:
    for directory in (Path.cwd(), *Path.cwd().parents):
        env_file = directory / ".env"
        if env_file.is_file():
            with env_file.open() as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    os.environ.setdefault(key, value)
            break


_load_dotenv()

if TYPE_CHECKING:
    from kiln.graph.executor import ExecutionGraph

import typer  # noqa: E402
from beads import BeadStore, GraphNode, PreflightBead, PRBead, WorkBead  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.table import Table  # noqa: E402

from kiln.config import Config, Registry, RepoEntry  # noqa: E402
from kiln.health import run_health_checks  # noqa: E402
from kiln.logger import Logger  # noqa: E402
from kiln.spec import parse_campaign, parse_spec  # noqa: E402

app = typer.Typer(
    name="kiln",
    help="Platform build orchestrator — spec-driven, bead-tracked, multi-repo.",
    no_args_is_help=True,
)
console = Console()


repo_app = typer.Typer(help="Manage platform repo registry.")
app.add_typer(repo_app, name="repo")

graph_app = typer.Typer(help="Inspect and manage graph execution nodes.")
app.add_typer(graph_app, name="graph")

bead_app = typer.Typer(help="Read and write beads from CC orchestrator sessions.")
app.add_typer(bead_app, name="bead")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_repo(repo: str | None) -> str:
    if repo:
        return repo
    # Try to detect from git remote
    r = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    console.print("[red]error:[/red] --repo is required (or run from inside a git repo)")
    raise typer.Exit(1)


_KILN_BOT = "yeast-bot"


_CI_WORKFLOW_TEMPLATE = """\
name: CI

on:
  push:
    branches: ["{branch}"]
  pull_request:
    branches: ["{branch}"]

jobs:
  test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - name: Run tests
        run: uv run pytest
      - name: Lint
        run: uv run ruff check
"""

_REQUIRED_LABELS = [
    ("stage/impl", "0075ca", "Implementation task"),
    ("stage/research", "0075ca", "Research task"),
    ("stage/design", "0075ca", "Design task"),
    ("P0", "b60205", "Release blocker"),
    ("P1", "d93f0b", "High priority"),
    ("P2", "e4e669", "Normal priority"),
    ("P3", "0e8a16", "Low priority"),
    ("P4", "c5def5", "Backlog"),
    ("in-progress", "f9d0c4", "Claimed by an agent"),
    ("bug", "d73a4a", "Something isn't working"),
    ("triage", "e4e669", "Pending triage"),
]


def _accept_bot_invitation(repo: str, token: str) -> None:
    """Accept the pending collaborator invitation for *repo* as yeast-bot.

    Uses *token* (KILN_GH_TOKEN) to authenticate as the bot user and
    calls the GitHub Invitations API.  Silently no-ops if no invitation exists.
    """
    import base64 as _base64  # noqa: F401 — kept for future use; suppress unused warning

    list_r = subprocess.run(
        [
            "curl",
            "-s",
            "-H",
            f"Authorization: token {token}",
            "https://api.github.com/user/repository_invitations",
        ],
        capture_output=True,
        text=True,
    )
    try:
        invitations = json.loads(list_r.stdout)
        matching = [
            inv["id"]
            for inv in invitations
            if isinstance(inv, dict) and inv.get("repository", {}).get("full_name", "") == repo
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return

    for inv_id in matching:
        accept = subprocess.run(
            [
                "curl",
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-X",
                "PATCH",
                "-H",
                f"Authorization: token {token}",
                f"https://api.github.com/user/repository_invitations/{inv_id}",
            ],
            capture_output=True,
            text=True,
        )
        http_code = accept.stdout.strip()
        if http_code not in ("204", ""):
            console.print(
                f"  [yellow]warning:[/yellow] invitation {inv_id} accept returned HTTP {http_code}"
            )


def _add_bot_collaborator(repo: str) -> None:
    """Add yeast-bot as a push collaborator on *repo* and auto-accept the invitation.

    The PUT call runs as the repo owner (ambient gh credentials — GH_TOKEN stripped
    so we don't accidentally auth as the bot).  The invitation is then accepted via
    KILN_GH_TOKEN.
    """
    import os as _os

    env = {k: v for k, v in _os.environ.items() if k != "GH_TOKEN"}
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/collaborators/{_KILN_BOT}",
            "-X",
            "PUT",
            "-f",
            "permission=push",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        console.print(
            f"  [yellow]warning:[/yellow] could not add {_KILN_BOT} to {repo}: "
            f"{result.stderr.strip()}"
        )
        return

    console.print(f"  {_KILN_BOT} added as collaborator on {repo}")

    token = _os.environ.get("KILN_GH_TOKEN") or ""
    if not token:
        console.print(
            f"  [yellow]warning:[/yellow] KILN_GH_TOKEN not set; "
            f"cannot auto-accept invitation for {_KILN_BOT}"
        )
        return

    _accept_bot_invitation(repo, token)
    console.print(f"  {_KILN_BOT} accepted invitation to {repo}")


def _install_ci_workflow(repo: str, branch: str = "mainline") -> None:
    """Install a basic CI workflow on *repo* if one does not already exist."""
    import base64

    # Check if ci.yml already exists
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/contents/.github/workflows/ci.yml"],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return  # already exists — _ensure_ci_auth will patch it if needed

    content = _CI_WORKFLOW_TEMPLATE.format(branch=branch)
    encoded = base64.b64encode(content.encode()).decode()
    subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/contents/.github/workflows/ci.yml",
            "-X",
            "PUT",
            "-f",
            "message=ci: install kiln CI workflow",
            "-f",
            f"content={encoded}",
        ],
        capture_output=True,
        text=True,
    )


def _init_empty_repo(repo: str) -> None:
    """Create an initial empty commit on repos with no commits, using 'mainline' as default."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="kiln-init-") as tmpdir:
        tmppath = Path(tmpdir)
        subprocess.run(
            ["gh", "repo", "clone", repo, "."],
            cwd=tmppath,
            capture_output=True,
            text=True,
        )
        subprocess.run(["git", "checkout", "-b", "mainline"], cwd=tmppath, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "chore: initialize repository"],
            cwd=tmppath,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "mainline"],
            cwd=tmppath,
            capture_output=True,
            text=True,
        )
        # Set the GitHub default branch to mainline
        subprocess.run(
            ["gh", "api", f"repos/{repo}", "-X", "PATCH", "-f", "default_branch=mainline"],
            capture_output=True,
            text=True,
        )


def _ensure_ci_auth(repo: str) -> None:
    """Patch ci.yml to authenticate sibling repo clones with GITHUB_TOKEN. Idempotent."""
    import base64

    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/contents/.github/workflows/ci.yml"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return  # no ci.yml — nothing to patch
    try:
        data = json.loads(r.stdout)
        content = base64.b64decode(data["content"]).decode("utf-8")
        sha = data["sha"]
    except (json.JSONDecodeError, KeyError, Exception):
        return

    # Already patched or no unauthenticated sibling clones
    if "x-access-token:${GH_TOKEN}@github.com" in content:
        return
    if "git clone https://github.com/" not in content:
        return

    # Patch: wrap git clone steps with GH_TOKEN env and use token URL
    import re

    def _patch_clone_step(m: re.Match) -> str:
        block = m.group(0)
        # Already has GH_TOKEN env
        if "GH_TOKEN" in block:
            return block
        # Add env block and rewrite URLs
        block = block.replace(
            "git clone https://github.com/",
            "git clone https://x-access-token:${GH_TOKEN}@github.com/",
        )
        # Insert env: block before `run:` in this step
        block = re.sub(
            r"(\s+run:\s*\|)",
            r"\n        env:\n          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\1",
            block,
            count=1,
        )
        return block

    patched = re.sub(
        r"- name: Clone sibling deps\n(?:[ \t]+.*\n)*?(?=[ \t]*-[ \t]|\Z)",
        _patch_clone_step,
        content,
        flags=re.MULTILINE,
    )

    if patched == content:
        return  # regex didn't match anything — leave it alone

    encoded = base64.b64encode(patched.encode("utf-8")).decode("ascii")
    subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/contents/.github/workflows/ci.yml",
            "-X",
            "PUT",
            "-f",
            "message=fix(ci): authenticate sibling dep clones with GITHUB_TOKEN",
            "-f",
            f"content={encoded}",
            "-f",
            f"sha={sha}",
        ],
        capture_output=True,
        text=True,
    )


def _patch_ci_runs_on(repo: str) -> None:
    """Patch ci.yml to use self-hosted runner instead of ubuntu-latest. Idempotent."""
    import base64

    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/contents/.github/workflows/ci.yml"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return
    try:
        data = json.loads(r.stdout)
        content = base64.b64decode(data["content"]).decode("utf-8")
        sha = data["sha"]
    except (json.JSONDecodeError, KeyError, Exception):
        return

    if "self-hosted" in content:
        return  # already patched

    patched = content.replace("runs-on: ubuntu-latest", "runs-on: self-hosted")
    if patched == content:
        return

    encoded = base64.b64encode(patched.encode("utf-8")).decode("ascii")
    subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/contents/.github/workflows/ci.yml",
            "-X",
            "PUT",
            "-f",
            "message=ci: use self-hosted runner",
            "-f",
            f"content={encoded}",
            "-f",
            f"sha={sha}",
        ],
        capture_output=True,
        text=True,
    )


def _register_runner(repo: str) -> None:
    """Register and start a self-hosted runner for *repo* on this machine. Idempotent."""
    import platform
    import tarfile
    import urllib.request

    # Check if an online runner already exists
    r = subprocess.run(
        [
            "gh", "api", f"repos/{repo}/actions/runners",
            "--jq", '[.runners[] | select(.status == "online")] | length',
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0 and r.stdout.strip() not in ("0", ""):
        return  # already have an online runner

    runner_base = Path.home() / "actions-runner"
    sanitized = repo.replace("/", "-")
    runner_dir = runner_base / sanitized
    runner_dir.mkdir(parents=True, exist_ok=True)

    # Detect platform
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        os_tag = "osx"
        arch_tag = "arm64" if machine == "arm64" else "x64"
    else:
        os_tag = "linux"
        arch_tag = "arm64" if machine in ("aarch64", "arm64") else "x64"

    # Get latest runner version
    ver_r = subprocess.run(
        ["gh", "api", "repos/actions/runner/releases/latest", "--jq", ".tag_name"],
        capture_output=True,
        text=True,
    )
    version = ver_r.stdout.strip().lstrip("v") if ver_r.returncode == 0 else "2.322.0"

    tarball_name = f"actions-runner-{os_tag}-{arch_tag}-{version}.tar.gz"
    tarball_path = runner_base / tarball_name

    config_sh = runner_dir / "config.sh"
    if not config_sh.exists():
        if not tarball_path.exists():
            url = f"https://github.com/actions/runner/releases/download/v{version}/{tarball_name}"
            urllib.request.urlretrieve(url, tarball_path)
        with tarfile.open(tarball_path) as tf:
            tf.extractall(runner_dir)

    # Get registration token
    token_r = subprocess.run(
        [
            "gh", "api", f"repos/{repo}/actions/runners/registration-token",
            "--method", "POST", "--jq", ".token",
        ],
        capture_output=True,
        text=True,
    )
    if token_r.returncode != 0:
        return
    token = token_r.stdout.strip()

    runner_name = f"kiln-{sanitized}"
    subprocess.run(
        [
            str(config_sh),
            "--unattended",
            "--url", f"https://github.com/{repo}",
            "--token", token,
            "--name", runner_name,
            "--labels", "self-hosted",
            "--replace",
        ],
        cwd=runner_dir,
        capture_output=True,
        text=True,
    )

    # Install as a launchd user agent (no sudo required)
    label = f"com.github.actions.runner.{sanitized}"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist_path.exists():
        log_dir = runner_dir / "_diag"
        log_dir.mkdir(exist_ok=True)
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{runner_dir}/run.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{runner_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/runner.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/runner.stderr.log</string>
</dict>
</plist>
"""
        plist_path.write_text(plist_content)
        subprocess.run(
            ["launchctl", "load", str(plist_path)],
            capture_output=True,
            text=True,
        )


def _scaffold_repo(repo: str) -> None:
    """Ensure all required labels exist on the repo and the repo has at least one commit. Idempotent."""
    # Initialize empty repos before creating labels (labels fail on empty repos too)
    r = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "isEmpty,defaultBranchRef"],
        capture_output=True,
        text=True,
    )
    try:
        info = json.loads(r.stdout)
        if info.get("isEmpty") or not info.get("defaultBranchRef", {}).get("name"):
            _init_empty_repo(repo)
    except (json.JSONDecodeError, KeyError):
        pass

    # Install CI workflow if missing, then patch auth and runner target
    _install_ci_workflow(repo, branch="mainline")
    _ensure_ci_auth(repo)
    _patch_ci_runs_on(repo)
    _register_runner(repo)

    # Get existing labels
    r = subprocess.run(
        ["gh", "label", "list", "--repo", repo, "--json", "name", "--limit", "100"],
        capture_output=True,
        text=True,
    )
    try:
        existing = {item["name"] for item in json.loads(r.stdout)}
    except (json.JSONDecodeError, KeyError):
        existing = set()

    for name, color, description in _REQUIRED_LABELS:
        if name not in existing:
            subprocess.run(
                [
                    "gh",
                    "label",
                    "create",
                    name,
                    "--repo",
                    repo,
                    "--color",
                    color,
                    "--description",
                    description,
                ],
                capture_output=True,
                text=True,
            )


def _get_store(config: Config) -> BeadStore:
    return BeadStore(config.beads_dir, config.repo)


def _get_logger(config: Config, run_id: str | None = None) -> Logger:
    log_dir = config.beads_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return Logger(log_dir / f"{config.repo.replace('/', '_')}.jsonl", run_id=run_id)


def _get_open_issues_for_milestone(repo: str, milestone: str) -> list[dict]:
    r = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--milestone",
            milestone,
            "--state",
            "open",
            "--json",
            "number,title,labels",
            "--limit",
            "200",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


def _file_issue(repo: str, title: str, body: str, milestone: str, labels: list[str]) -> int | None:
    cmd = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body",
        body,
        "--milestone",
        milestone,
    ]
    for label in labels:
        cmd += ["--label", label]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    # Parse issue number from URL
    url = r.stdout.strip()
    try:
        return int(url.rstrip("/").split("/")[-1])
    except (ValueError, IndexError):
        return None


def _ensure_milestone(repo: str, milestone: str) -> bool:
    # Check if it exists
    r = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/milestones",
            "--jq",
            f'[.[] | select(.title=="{milestone}")] | length',
        ],
        capture_output=True,
        text=True,
    )
    try:
        count = int(r.stdout.strip())
        if count > 0:
            return True
    except (ValueError, TypeError):
        pass
    # Create it
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/milestones", "--method", "POST", "-f", f"title={milestone}"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def _seed_work_beads(
    store: BeadStore,
    issues: list[dict],
    milestone: str,
    spec_file: str | None = None,
    repo: str = "",
) -> list[int]:
    """Create WorkBeads for issues that don't already have one. Returns new issue numbers."""
    from beads import WorkBead

    new_numbers = []
    for issue in issues:
        n = issue["number"]
        existing = store.read_work_bead(n)
        if existing is not None:
            # Always sync the title from GitHub — the issue may have been renamed
            # after the bead was first created (e.g. plan handler renames module issues).
            if existing.title != issue["title"]:
                existing.title = issue["title"]
                store.write_work_bead(existing)
            continue
        bead = WorkBead(
            issue_number=n,
            repo=repo,
            title=issue["title"],
            milestone=milestone,
            spec_file=spec_file,
        )
        store.write_work_bead(bead)
        new_numbers.append(n)
    return new_numbers


def _print_dry_run_summary(
    milestone: str,
    graph: ExecutionGraph,
    store: BeadStore | None,
) -> None:
    """Print a rich summary table of the dry-run plan output."""

    console.print(f"\n[bold yellow][dry-run] Plan summary for {milestone}[/bold yellow]")

    # Find the most recent done plan node (prefer plan-refine over initial plan)
    # and collect all initial unknowns to compute what was resolved.
    all_done_plans = [
        n for n in graph.all_nodes() if n.type == "plan" and n.state == "done"
    ]
    # Sort by completed_at descending; fall back to id-based ordering (refine > initial)
    all_done_plans.sort(
        key=lambda n: (n.completed_at or n.created_at, "refine" in n.id),
        reverse=True,
    )
    plan_node = all_done_plans[0] if all_done_plans else None
    initial_plan = all_done_plans[-1] if all_done_plans else None

    build_nodes = [n for n in graph.all_nodes() if n.type == "build"]

    if plan_node and plan_node.output.get("artifact"):
        artifact = plan_node.output["artifact"]
        console.print(f"[dim]Approach:[/dim] {artifact.get('approach', '')}")
        console.print(
            f"[dim]Confidence:[/dim] {artifact.get('confidence', 0):.0%}  "
            f"[dim]Risk:[/dim] {', '.join(artifact.get('risk_flags', [])) or 'none'}"
        )
        # Show unknowns that were resolved (initial unknowns minus remaining)
        initial_unknowns = set(
            (initial_plan.output.get("artifact") or {}).get("unknowns", [])
        ) if initial_plan else set()
        remaining_unknowns = set(artifact.get("unknowns", []))
        resolved = initial_unknowns - remaining_unknowns
        if resolved:
            console.print(f"[dim]Unknowns resolved:[/dim] {', '.join(list(resolved)[:3])}")
        if remaining_unknowns:
            console.print(f"[dim]Remaining unknowns:[/dim] {', '.join(list(remaining_unknowns)[:3])}")

    if not build_nodes:
        console.print("[yellow]No build nodes emitted — check plan output above.[/yellow]")
        return

    table = Table(title="Work Beads (not dispatched)", show_lines=True)
    table.add_column("Module", style="bold")
    table.add_column("Issue #")
    table.add_column("Files")
    table.add_column("Bead State")

    for node in sorted(build_nodes, key=lambda n: n.id):
        module = node.context.get("module", node.id)
        issue_number = node.context.get("issue_number")
        files = node.context.get("files", [])
        bead_state = "—"
        if issue_number and store:
            bead = store.read_work_bead(issue_number)
            bead_state = bead.state if bead else "missing"
        table.add_row(
            module,
            f"#{issue_number}" if issue_number else "—",
            "\n".join(files[:6]) + ("\n…" if len(files) > 6 else ""),
            bead_state,
        )

    console.print(table)
    console.print(
        f"\n[green]{len(build_nodes)} work bead(s) created[/green] — "
        "run without [bold]--dry-run[/bold] to dispatch agents."
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def run(
    specs: Annotated[list[Path], typer.Argument(help="Spec markdown file(s) to run.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo to operate on.")] = None,
    concurrency: Annotated[int, typer.Option(help="Max parallel agents.")] = 3,
    model: Annotated[str | None, typer.Option(help="Override model for all agents.")] = None,
    milestone: Annotated[str | None, typer.Option(help="GitHub milestone name.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    tracking_issue: Annotated[
        bool,
        typer.Option(
            "--tracking-issue/--no-tracking-issue",
            help="File a milestone tracking issue on GitHub (closed when milestone completes).",
        ),
    ] = True,
    max_budget: Annotated[
        float | None,
        typer.Option("--max-budget", help="Stop if total LLM cost exceeds this amount (USD)."),
    ] = None,
    max_research_rounds: Annotated[
        int,
        typer.Option("--max-research-rounds", help="Max research rounds before hard-failing on low confidence (default 2)."),
    ] = 2,
) -> None:
    """Parse spec(s), file GitHub issues, and dispatch agents."""
    import os as _os

    from kiln.graph.lock import OrchestratorLock

    repo = _require_repo(repo)
    config = Config.from_env(repo)
    if concurrency:
        config.concurrency = concurrency
    if model:
        config.model = model
    config.max_research_rounds = max_research_rounds

    # Health check runs with operator credentials so gh-auth passes cleanly.
    report = run_health_checks(repo)
    if not report.healthy:
        for c in report.fatal:
            console.print(f"[red]FATAL[/red] {c.name}: {c.message}")
        raise typer.Exit(1)

    # After health checks pass, switch all gh ops to yeast-bot for the rest of
    # this process (orchestrator + agent subprocesses).
    if config.github_token:
        _os.environ["GH_TOKEN"] = config.github_token

    # Scaffold repo: labels, default branch protection
    if not dry_run:
        console.print(f"Scaffolding {repo}...")
        _scaffold_repo(repo)

    owner, repo_name = config.repo.split("/", 1)
    with OrchestratorLock(owner=owner, repo=repo_name):
        _run_locked(
            specs=specs,
            repo=repo,
            config=config,
            milestone=milestone,
            dry_run=dry_run,
            tracking_issue=tracking_issue,
            max_budget=max_budget,
        )


def _run_locked(
    specs: list[Path],
    repo: str,
    config: Config,
    milestone: str | None,
    dry_run: bool,
    tracking_issue: bool,
    max_budget: float | None,
) -> None:
    """Execute the run body after acquiring the orchestrator lock."""
    store = _get_store(config)
    logger = _get_logger(config)

    all_issue_numbers: list[int] = []
    _spec_paths: list[tuple[Path, str, int | None]] = []  # (spec_path, milestone, issue_number)

    for spec_path in specs:
        if not spec_path.exists():
            console.print(f"[red]error:[/red] spec file not found: {spec_path}")
            raise typer.Exit(1)

        # Handle campaign files
        if spec_path.name == "campaign.md" or (
            spec_path.suffix == ".md" and "campaign" in spec_path.stem
        ):
            sub_specs = parse_campaign(spec_path)
            if not sub_specs:
                console.print(f"[yellow]warning:[/yellow] no specs found in campaign {spec_path}")
                continue
            for sub_path in sub_specs:
                if sub_path.exists():
                    issue_numbers = _run_single_spec(
                        sub_path, repo, config, store, logger, milestone, dry_run, tracking_issue
                    )
                    all_issue_numbers.extend(issue_numbers)
                    if issue_numbers:
                        ms = milestone or parse_spec(sub_path).version
                        milestone_issue = issue_numbers[0] if issue_numbers[0] != -1 else None
                        _spec_paths.append((sub_path, ms, milestone_issue))
        else:
            issue_numbers = _run_single_spec(
                spec_path, repo, config, store, logger, milestone, dry_run, tracking_issue
            )
            all_issue_numbers.extend(issue_numbers)
            if issue_numbers:
                ms = milestone or parse_spec(spec_path).version
                milestone_issue = issue_numbers[0] if issue_numbers[0] != -1 else None
                _spec_paths.append((spec_path, ms, milestone_issue))

    if not all_issue_numbers and not _spec_paths:
        console.print("No issues to dispatch.")
        return

    console.print(
        f"\nDispatching {len(_spec_paths)} spec(s) via graph executor "
        f"(concurrency={config.concurrency})..."
    )

    from kiln.graph.builder import build_greenfield_graph
    from kiln.graph.executor import GraphExecutor, make_handlers

    if _spec_paths:
        handlers = make_handlers(store=store, logger=logger)
        executor = GraphExecutor(
            config=config,
            handlers=handlers,
            store=store,
            logger=logger,
            concurrency=config.concurrency,
            watchdog_interval=float(config.watchdog_interval_seconds),
            dry_run=dry_run,
            max_budget_usd=max_budget,
        )

        # Clone the repo once for codebase assessment by the plan handler
        import tempfile

        repo_clone_dir = tempfile.mkdtemp(prefix="kiln-clone-")
        clone_result = subprocess.run(
            ["gh", "repo", "clone", config.repo, repo_clone_dir, "--", "--depth=1"],
            capture_output=True,
            text=True,
        )
        repo_local_path = repo_clone_dir if clone_result.returncode == 0 else ""
        if not repo_local_path:
            console.print(
                f"[yellow]warning:[/yellow] could not clone {config.repo} for codebase assessment"
            )

        async def _run_graph() -> None:
            for spec_path, ms, milestone_issue in _spec_paths:
                graph = build_greenfield_graph(
                    milestone=ms,
                    spec_file=spec_path,
                    repo=config.repo,
                    repo_local_path=repo_local_path,
                    milestone_issue_number=milestone_issue,
                )
                if dry_run:
                    console.print(
                        f"  [yellow][dry-run][/yellow] planning {ms} (research + plan LLMs will run)..."
                    )
                else:
                    console.print(f"  executing graph for {ms}...")
                result = await executor.run(graph)
                if dry_run:
                    _print_dry_run_summary(ms, graph, store)
                else:
                    console.print(
                        f"  {ms}: done={len(result.done)} failed={len(result.failed)} "
                        f"abandoned={len(result.abandoned)}"
                    )
                    for nid in result.abandoned:
                        node = graph.get_node(nid)
                        err = (node.output or {}).get("_error") if node else None
                        if err:
                            console.print(f"  [red]  {nid}:[/red] {err}")

        asyncio.run(_run_graph())
    else:
        # Legacy: rolling dispatcher for issue-number-only runs
        from kiln.dispatch import RollingDispatcher
        from kiln.merge import process_merge_queue

        dispatcher = RollingDispatcher(config, store, logger)

        async def _run() -> None:
            dispatch_task = asyncio.create_task(dispatcher.run(all_issue_numbers))

            heartbeat_interval = config.watchdog_interval_seconds

            async def _heartbeat() -> None:
                while not dispatch_task.done():
                    await asyncio.sleep(heartbeat_interval)
                    queue = store.read_merge_queue()
                    logger.heartbeat(
                        active_agents=dispatcher.active_count,
                        queue_depth=len(queue.items),
                        completed=dispatcher.completed_count,
                        cost_usd=0.0,
                    )
                    merged = process_merge_queue(store, config, logger=logger)
                    if merged:
                        console.print(f"  merged {merged} PR(s)")

            await asyncio.gather(dispatch_task, _heartbeat())
            process_merge_queue(store, config, logger=logger)

        asyncio.run(_run())
        console.print(f"\n[green]Done.[/green] Completed: {dispatcher.completed_count}")


def _run_single_spec(
    spec_path: Path,
    repo: str,
    config: Config,
    store: BeadStore,
    logger: Logger,
    milestone_override: str | None,
    dry_run: bool,
    tracking_issue: bool = True,
) -> list[int]:
    spec = parse_spec(spec_path)
    ms = milestone_override or f"{spec.version}"

    console.print(f"\n[bold]{spec.title}[/bold] → milestone: {ms}")

    # Guard: warn if spec project name doesn't match the target repo
    repo_name = repo.split("/")[-1].lower()
    spec_project = spec.project.lower()
    if spec_project not in repo_name and repo_name not in spec_project:
        console.print(
            f"[yellow]warning:[/yellow] spec project [bold]{spec.project!r}[/bold] "
            f"does not match target repo [bold]{repo!r}[/bold]"
        )
        if not typer.confirm("Continue anyway?", default=False):
            return []

    # Ensure milestone exists
    if not dry_run and not _ensure_milestone(repo, ms):
        console.print(f"[red]error:[/red] could not create milestone {ms}")
        return []

    # Build issue body from spec
    body = f"{spec.overview}\n\n"
    if spec.success_criteria:
        body += "## Success Criteria\n"
        for c in spec.success_criteria:
            body += f"- [ ] {c}\n"
    body += f"\n_Spec: `{spec_path.name}`_"

    issue_numbers: list[int] = []

    if tracking_issue:
        # File one stub issue as the milestone tracking ticket.
        existing = _get_open_issues_for_milestone(repo, ms) if not dry_run else []
        impl_issues = [i for i in existing if "stage/impl" in str(i.get("labels", []))]

        if not impl_issues:
            if dry_run:
                console.print(f"  [dry-run] would file tracking issue: {spec.issue_title}")
                return [-1]  # sentinel

            issue_number = _file_issue(
                repo,
                title=spec.issue_title,
                body=body,
                milestone=ms,
                labels=["stage/impl", "P2"],
            )
            if issue_number:
                console.print(f"  filed tracking issue #{issue_number}: {spec.issue_title}")
                issue_numbers.append(issue_number)
        else:
            issue_numbers = [i["number"] for i in impl_issues]
            console.print(f"  using existing tracking issues: {issue_numbers}")
    else:
        if dry_run:
            console.print("  [dry-run] skipping tracking issue (--no-tracking-issue)")
            return [-1]  # sentinel
        console.print("  [dim]tracking issue skipped (--no-tracking-issue)[/dim]")

    # Seed WorkBeads
    issues_data = [{"number": n, "title": spec.issue_title} for n in issue_numbers]
    new_beads = _seed_work_beads(store, issues_data, ms, str(spec_path), repo)
    if new_beads:
        console.print(f"  seeded {len(new_beads)} work bead(s)")

    logger.info(f"spec {spec_path.name} → {len(issue_numbers)} issue(s)", milestone=ms)
    return issue_numbers if issue_numbers else [-1]  # return sentinel so caller adds to _spec_paths


@app.command()
def plan(
    specs: Annotated[list[Path], typer.Argument(help="Spec file(s) to plan (no dispatch).")],
    repo: Annotated[str | None, typer.Option()] = None,
    milestone_prefix: Annotated[str | None, typer.Option()] = None,
) -> None:
    """File GitHub issues for specs without dispatching agents.

    Seeds WorkBeads and records campaign ordering.
    """
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    _scaffold_repo(repo)

    from beads import CampaignBead, MilestonePlan

    campaign = store.read_campaign_bead() or CampaignBead(repo=repo)
    total_filed = 0

    for i, spec_path in enumerate(specs):
        if not spec_path.exists():
            console.print(f"[red]error:[/red] spec not found: {spec_path}")
            raise typer.Exit(1)
        spec = parse_spec(spec_path)
        ms = f"{spec.version}"

        # Check ordering (basic semver guard)
        if i > 0 and campaign.milestones:
            pass  # TODO: full semver ordering validation

        console.print(f"Planning {spec.title} → {ms}")
        _ensure_milestone(repo, ms)

        # File impl issue
        body = f"{spec.overview}\n\n_Spec: `{spec_path.name}`_"
        issue_number = _file_issue(
            repo,
            title=spec.issue_title,
            body=body,
            milestone=ms,
            labels=["stage/impl", "P2"],
        )
        if issue_number:
            console.print(f"  filed #{issue_number}")
            total_filed += 1
            bead_data = [{"number": issue_number, "title": spec.issue_title}]
            _seed_work_beads(store, bead_data, ms, str(spec_path), repo)

        # Record in campaign
        if not campaign.get_milestone(ms, repo):
            plan_entry = MilestonePlan(milestone=ms, repo=repo, spec_file=str(spec_path))
            campaign.milestones.append(plan_entry)

    store.write_campaign_bead(campaign)
    console.print(f"\nPlanned {total_filed} issue(s). Campaign updated.")


@app.command()
def preflight(
    spec: Annotated[Path, typer.Argument(help="Spec markdown file to evaluate.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo for codebase context.")] = None,
    repo_path: Annotated[str | None, typer.Option(help="Local path to repo checkout.")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Decide whether a spec should run in Claude Code (CC) or kiln (autonomous batch).

    Scores the spec on four signals — volume, novelty, ambiguity, cross_cutting —
    and recommends a route. No side effects; does not start any work.
    """
    import asyncio as _asyncio

    from kiln.preflight import run_preflight

    if not spec.exists():
        console.print(f"[red]error:[/red] spec not found: {spec}")
        raise typer.Exit(1)

    spec_text = spec.read_text(encoding="utf-8")

    # Resolve local repo path for codebase context
    local_path = repo_path
    if not local_path and repo:
        # Try common checkout locations
        for candidate in (
            Path.cwd(),
            Path.home() / "Documents" / "dev" / "github" / repo,
            Path.home() / repo.split("/")[-1],
        ):
            if candidate.is_dir() and (candidate / ".git").exists():
                local_path = str(candidate)
                break

    result = _asyncio.run(run_preflight(spec_text, local_path))

    # Write preflight bead and fire hook (best-effort, non-fatal).
    if repo:
        try:
            import uuid
            from kiln.hooks import fire

            config = Config.from_env(repo)
            store = _get_store(config)
            pfbead = PreflightBead(
                id=str(uuid.uuid4()),
                repo=repo,
                spec_file=str(spec),
                route=result.route,
                score=result.total,
                confidence=result.confidence,
                volume=result.volume.score,
                novelty=result.novelty.score,
                ambiguity=result.ambiguity.score,
                cross_cutting=result.cross_cutting.score,
                summary=result.summary,
            )
            store.write_preflight_bead(pfbead)
            fire("on-preflight", {
                "KILN_REPO": repo,
                "KILN_SPEC": str(spec),
                "KILN_ROUTE": result.route,
                "KILN_SCORE": str(result.total),
                "KILN_CONFIDENCE": f"{result.confidence:.2f}",
            })
        except Exception:
            pass

    if json_output:
        import dataclasses
        console.print(json.dumps({
            "route": result.route,
            "total": result.total,
            "confidence": round(result.confidence, 2),
            "summary": result.summary,
            "signals": {
                "volume": {"score": result.volume.score, "reason": result.volume.reason},
                "novelty": {"score": result.novelty.score, "reason": result.novelty.reason},
                "ambiguity": {"score": result.ambiguity.score, "reason": result.ambiguity.reason},
                "cross_cutting": {"score": result.cross_cutting.score, "reason": result.cross_cutting.reason},
            },
        }, indent=2))
        return

    route_color = "green" if result.route == "kiln" else "yellow"
    route_label = "kiln (autonomous batch)" if result.route == "kiln" else "Claude Code (interactive)"
    console.print(f"\nRoute: [{route_color}]{route_label}[/{route_color}]  (score {result.total}/8, confidence {result.confidence:.0%})\n")

    signal_rows = [
        ("volume",        result.volume),
        ("novelty",       result.novelty),
        ("ambiguity",     result.ambiguity),
        ("cross_cutting", result.cross_cutting),
    ]
    for name, sig in signal_rows:
        bar = "█" * sig.score + "░" * (2 - sig.score)
        console.print(f"  {name:<14} {bar}  {sig.score}/2  {sig.reason}")

    console.print(f"\n  {result.summary}")

    if result.route == "kiln":
        console.print(f"\n  Run: [bold]kiln run {spec}[/bold]")
    else:
        console.print(f"\n  Open Claude Code with spec: [bold]{spec}[/bold]")


@app.command()
def init(
    milestone: Annotated[str, typer.Option(help="Milestone name to create.")],
    repo: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Create a GitHub milestone (no issue seeding)."""
    repo = _require_repo(repo)
    if _ensure_milestone(repo, milestone):
        console.print(f"[green]ok[/green] milestone '{milestone}' exists in {repo}")
    else:
        console.print(f"[red]error:[/red] could not create milestone '{milestone}'")
        raise typer.Exit(1)


def _format_validate_state(node: GraphNode) -> str:
    """Format the state column for a validate node.

    Maps internal node state + output metadata to the canonical display format:
    - pending   → not yet started
    - running   → currently executing
    - passed    → done with no failures
    - failed(N) → done with N failing assertions
    """
    if node.state == "pending":
        return "pending"
    if node.state == "running":
        return "running"
    if node.state in ("done", "failed"):
        output = node.output or {}
        failed_count = output.get("failed_count")
        if failed_count is not None:
            n = int(failed_count)
            if n == 0:
                return "passed"
            return f"failed({n})"
        # Derive from assertions list if failed_count not stored directly
        assertions = node.context.get("assertions", [])
        passed = output.get("passed", [])
        if assertions and passed:
            n = len(assertions) - len(passed)
            if n <= 0:
                return "passed"
            return f"failed({n})"
        # Fall back based on node state
        if node.state == "done":
            return "passed"
        return "failed(0)"
    # abandoned / wont-do etc — show raw state
    return node.state


def _build_status_table(
    store: BeadStore,
    repo: str,
    milestone: str | None,
) -> Group:

    tables = []

    beads = store.list_work_beads(milestone=milestone)
    bead_colors = {
        "open": "dim",
        "claimed": "yellow",
        "pr_open": "blue",
        "merge_ready": "green",
        "closed": "green",
        "abandoned": "red",
    }
    bead_table = Table(
        title=f"Work Beads — {repo}" + (f" / {milestone}" if milestone else ""),
    )
    bead_table.add_column("Issue", style="cyan", justify="right")
    bead_table.add_column("Title")
    bead_table.add_column("State")
    bead_table.add_column("Retries", justify="right")
    bead_table.add_column("Branch")
    bead_table.add_column("PR", justify="right")

    def _model_tier(model: str | None) -> str:
        """Extract human-readable tier: opus / sonnet / haiku / ''."""
        if not model:
            return ""
        m = model.lower()
        if "opus" in m:
            return "opus"
        if "sonnet" in m:
            return "sonnet"
        if "haiku" in m:
            return "haiku"
        return model

    for bead in sorted(beads, key=lambda b: b.issue_number):
        color = bead_colors.get(bead.state, "white")
        bead_table.add_row(
            str(bead.issue_number),
            bead.title[:50],
            f"[{color}]{bead.state}[/{color}]",
            str(bead.retry_count) if bead.retry_count else "",
            bead.branch or "",
            str(bead.pr_number) if bead.pr_number else "",
        )
    tables.append(bead_table)

    all_nodes = store.list_nodes()
    nodes = [n for n in all_nodes if not milestone or n.id.startswith(f"{milestone}-")]
    if nodes:
        node_colors = {
            "pending": "dim",
            "running": "yellow",
            "done": "green",
            "already-done": "cyan",
            "failed": "red",
            "abandoned": "red",
            "wont-do": "dim",
        }
        node_table = Table(title="Graph Nodes")
        node_table.add_column("Node ID")
        node_table.add_column("Type")
        node_table.add_column("State")
        node_table.add_column("Model")
        node_table.add_column("Retries", justify="right")
        _TYPE_ORDER = {"plan": 0, "research": 1, "build": 2, "merge": 3, "readme": 4, "validate": 5}
        for node in sorted(nodes, key=lambda n: (_TYPE_ORDER.get(n.type, 6), n.id)):
            color = node_colors.get(node.state, "white")
            if node.type == "merge":
                node_model_display = "[dim]N/A[/dim]"
            else:
                node_model = (node.output or {}).get("model") or node.assigned_model or ""
                node_model_display = _model_tier(node_model)
            if node.type == "validate":
                state_display = f"[{color}]{_format_validate_state(node)}[/{color}]"
            else:
                state_display = f"[{color}]{node.state}[/{color}]"
            node_table.add_row(
                node.id,
                node.type,
                state_display,
                node_model_display,
                str(node.retry_count) if node.retry_count else "",
            )
        tables.append(node_table)

        # Estimated cost across all completed nodes
        total_cost = sum(
            (n.output or {}).get("cost_usd", 0.0)
            for n in nodes  # already filtered by milestone
            if n.state == "done" and (n.output or {}).get("cost_usd") is not None
        )
        if total_cost > 0:
            from rich.text import Text

            tables.append(Text(f"  Est. cost: ${total_cost:.2f}", style="dim"))

    return Group(*tables)


def _detect_latest_milestone(store: BeadStore) -> str | None:
    """Return the latest milestone slug from the campaign bead or work beads."""
    campaign = store.read_campaign_bead()
    if campaign and campaign.milestones:
        return campaign.milestones[-1].milestone
    milestones = sorted({b.milestone for b in store.list_work_beads() if b.milestone})
    return milestones[-1] if milestones else None


@app.command()
def status(
    repo: Annotated[str | None, typer.Option()] = None,
    milestone: Annotated[str | None, typer.Option()] = None,
    watch: Annotated[bool, typer.Option("--watch", "-w", help="Refresh every 3s.")] = False,
) -> None:
    """Show bead state table for the latest milestone. Use --watch for live refresh."""
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)
    if milestone is None:
        milestone = _detect_latest_milestone(store)

    if watch:
        import time

        from rich.live import Live

        with Live(console=console, refresh_per_second=1) as live:
            while True:
                live.update(_build_status_table(store, repo, milestone))
                time.sleep(3)
        return

    beads = store.list_work_beads(milestone=milestone)
    nodes = store.list_nodes()
    if not beads and not nodes:
        console.print("No beads or graph nodes found.")
        return

    # Campaign summary
    campaign = store.read_campaign_bead()
    if campaign and campaign.milestones:
        console.print(f"\n[bold]Campaign[/bold] ({len(campaign.milestones)} milestones)")
        for m in campaign.milestones:
            status_color = {
                "shipped": "green",
                "implementing": "yellow",
                "blocked": "red",
                "failed": "red",
                "pending": "dim",
            }.get(m.status, "white")
            console.print(f"  [{status_color}]{m.status:15}[/{status_color}] {m.milestone}")

    console.print(_build_status_table(store, repo, milestone))

    # Merge queue
    queue = store.read_merge_queue()
    if queue.items:
        console.print(f"\nMerge queue: {len(queue.items)} item(s)")
        for item in queue.items:
            console.print(
                f"  PR #{item.pr_number} (issue #{item.issue_number}, branch: {item.branch})"
            )


@app.command(name="beads")
def beads_cmd(
    repo: Annotated[str | None, typer.Option()] = None,
    state: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Show all beads for a repo."""
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    work_beads = store.list_work_beads(state=state)  # type: ignore
    pr_beads = store.list_pr_beads()

    console.print(f"\n[bold]Work Beads[/bold] ({len(work_beads)})")
    for b in sorted(work_beads, key=lambda x: x.issue_number):
        console.print(f"  #{b.issue_number:4}  {b.state:15}  {b.title[:50]}")

    console.print(f"\n[bold]PR Beads[/bold] ({len(pr_beads)})")
    for b in sorted(pr_beads, key=lambda x: x.pr_number):
        console.print(f"  PR #{b.pr_number:4}  {b.state:15}  issue #{b.issue_number}")


# ---------------------------------------------------------------------------
# kiln bead — CC orchestrator bead write commands
# ---------------------------------------------------------------------------


@bead_app.command(name="claim")
def bead_claim(
    issue: Annotated[int, typer.Argument(help="GitHub issue number.")],
    title: Annotated[str, typer.Argument(help="Issue title.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo")] = None,
    branch: Annotated[str | None, typer.Option(help="Branch name (e.g. 42-fix-bug).")] = None,
    milestone: Annotated[str | None, typer.Option(help="Milestone slug.")] = None,
) -> None:
    """Record a claimed issue as a WorkBead (state=claimed).

    Call this after `gh issue edit <N> --add-label in-progress`.
    Fires the on-claim hook if installed.
    """
    from kiln.hooks import fire

    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    existing = store.read_work_bead(issue)
    if existing:
        existing.state = "claimed"  # type: ignore[assignment]
        if branch:
            existing.branch = branch
        if milestone:
            existing.milestone = milestone
        store.write_work_bead(existing)
        bead = existing
    else:
        bead = WorkBead(
            issue_number=issue,
            repo=repo,
            title=title,
            state="claimed",
            branch=branch,
            milestone=milestone,
        )
        store.write_work_bead(bead)

    fire("on-claim", {
        "KILN_REPO": repo,
        "KILN_ISSUE": str(issue),
        "KILN_TITLE": title,
        "KILN_BRANCH": branch or "",
        "KILN_MILESTONE": milestone or "",
    })
    console.print(f"[green]claimed[/green]  #{issue}  {title[:60]}")


@bead_app.command(name="pr")
def bead_pr(
    issue: Annotated[int, typer.Argument(help="GitHub issue number.")],
    pr: Annotated[int, typer.Argument(help="GitHub PR number.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo")] = None,
    branch: Annotated[str | None, typer.Option(help="Branch name.")] = None,
) -> None:
    """Record a PR as a PRBead and update the WorkBead to pr_open.

    Call this after `gh pr create`.
    Fires the on-pr-open hook if installed.
    """
    from kiln.hooks import fire

    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    branch_val = branch or f"{issue}-branch"
    pr_bead = PRBead(pr_number=pr, repo=repo, issue_number=issue, branch=branch_val)
    store.write_pr_bead(pr_bead)

    work = store.read_work_bead(issue)
    if work:
        work.state = "pr_open"  # type: ignore[assignment]
        work.pr_number = pr
        if branch:
            work.branch = branch
        store.write_work_bead(work)

    fire("on-pr-open", {
        "KILN_REPO": repo,
        "KILN_ISSUE": str(issue),
        "KILN_PR": str(pr),
        "KILN_BRANCH": branch_val,
    })
    console.print(f"[green]pr-open[/green]  #{issue} → PR #{pr}")


@bead_app.command(name="merge")
def bead_merge(
    pr: Annotated[int, typer.Argument(help="GitHub PR number.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo")] = None,
) -> None:
    """Mark a PR as merged — update PRBead to merged, WorkBead to closed.

    Call this after `gh pr merge`.
    Fires the on-merge hook if installed.
    """
    from kiln.hooks import fire

    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    pr_bead = store.read_pr_bead(pr)
    issue_number = 0
    if pr_bead:
        issue_number = pr_bead.issue_number
        pr_bead.state = "merged"  # type: ignore[assignment]
        store.write_pr_bead(pr_bead)
        work = store.read_work_bead(pr_bead.issue_number)
        if work:
            work.state = "closed"  # type: ignore[assignment]
            store.write_work_bead(work)

    fire("on-merge", {
        "KILN_REPO": repo,
        "KILN_PR": str(pr),
        "KILN_ISSUE": str(issue_number),
    })
    console.print(f"[green]merged[/green]  PR #{pr}  (issue #{issue_number})")


@bead_app.command(name="abandon")
def bead_abandon(
    issue: Annotated[int, typer.Argument(help="GitHub issue number.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo")] = None,
) -> None:
    """Mark an issue as abandoned — update WorkBead to abandoned.

    Call this when cleaning up stale in-progress labels.
    Fires the on-abandon hook if installed.
    """
    from kiln.hooks import fire

    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    work = store.read_work_bead(issue)
    if work:
        work.state = "abandoned"  # type: ignore[assignment]
        store.write_work_bead(work)

    fire("on-abandon", {"KILN_REPO": repo, "KILN_ISSUE": str(issue)})
    console.print(f"[yellow]abandoned[/yellow]  #{issue}")


@bead_app.command(name="list")
def bead_list(
    repo: Annotated[str | None, typer.Option(help="owner/repo")] = None,
    state: Annotated[str | None, typer.Option(help="Filter by WorkBead state.")] = None,
) -> None:
    """List work and PR beads for a repo."""
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    work_beads = store.list_work_beads(state=state)  # type: ignore
    pr_beads = store.list_pr_beads()

    console.print(f"\n[bold]Work Beads[/bold] ({len(work_beads)})")
    for b in sorted(work_beads, key=lambda x: x.issue_number):
        console.print(f"  #{b.issue_number:4}  {b.state:15}  {b.title[:50]}")

    console.print(f"\n[bold]PR Beads[/bold] ({len(pr_beads)})")
    for b in sorted(pr_beads, key=lambda x: x.pr_number):
        console.print(f"  PR #{b.pr_number:4}  {b.state:15}  issue #{b.issue_number}")


@app.command()
def health(
    repo: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Run preflight health checks."""
    repo = _require_repo(repo)
    report = run_health_checks(repo)

    table = Table(title="Health Checks")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Message")

    for check in report.checks:
        color = {"pass": "green", "fail": "red", "warn": "yellow"}[check.status]
        table.add_row(check.name, f"[{color}]{check.status.upper()}[/{color}]", check.message)

    console.print(table)

    if not report.healthy:
        raise typer.Exit(1)


@app.command()
def monitor(
    repo: Annotated[str | None, typer.Option()] = None,
    once: Annotated[bool, typer.Option("--once")] = False,
    interval: Annotated[int, typer.Option(help="Scan interval in seconds.")] = 300,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Run the anomaly monitor loop."""
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)
    logger = _get_logger(config)

    from kiln.monitor import run_monitor

    console.print(f"Starting monitor for {repo} (interval={interval}s, once={once})")
    asyncio.run(
        run_monitor(
            store,
            config,
            logger,
            once=once,
            interval_seconds=interval,
            dry_run=dry_run,
        )
    )


@app.command(name="spec")
def spec_cmd(
    description: Annotated[str | None, typer.Argument(help="Feature description.")] = None,
    file: Annotated[Path | None, typer.Option("--file", help="Read description from file.")] = None,
    repo: Annotated[str | None, typer.Option()] = None,
    output_dir: Annotated[Path | None, typer.Option()] = None,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
) -> None:
    """Interactive spec-forge: draft a milestone spec from a description."""
    registry = Registry()

    from kiln.forge import spec_forge

    written = asyncio.run(
        spec_forge(
            description=description,
            file=file,
            registry=registry,
            interactive=not non_interactive,
            output_dir=output_dir or Path.cwd(),
        )
    )

    for path in written:
        console.print(f"[green]wrote:[/green] {path}")


@app.command(name="run-issue")
def run_issue(
    issue: Annotated[int, typer.Option(help="GitHub issue number to dispatch.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo to operate on.")] = None,
    concurrency: Annotated[int, typer.Option(help="Max parallel agents.")] = 1,
    model: Annotated[str | None, typer.Option(help="Override model for all agents.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Dispatch a single issue by number. Used by the GitHub Actions pipeline."""
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    if concurrency:
        config.concurrency = concurrency
    if model:
        config.model = model

    # Fetch issue metadata
    r = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(issue),
            "--repo",
            repo,
            "--json",
            "title,milestone,labels",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        console.print(f"[red]error:[/red] could not fetch issue #{issue}: {r.stderr.strip()}")
        raise typer.Exit(1)
    try:
        issue_data = json.loads(r.stdout)
    except json.JSONDecodeError:
        console.print(f"[red]error:[/red] could not parse issue #{issue} response")
        raise typer.Exit(1) from None

    milestone_title: str = (issue_data.get("milestone") or {}).get("title", "")
    issue_title: str = issue_data.get("title", f"Issue #{issue}")

    console.print(f"Dispatching issue #{issue}: {issue_title}")
    if milestone_title:
        console.print(f"  milestone: {milestone_title}")

    if dry_run:
        console.print("[yellow][dry-run][/yellow] would dispatch issue", issue)
        return

    # Health check
    report = run_health_checks(repo)
    if not report.healthy:
        for c in report.fatal:
            console.print(f"[red]FATAL[/red] {c.name}: {c.message}")
        raise typer.Exit(1)

    store = _get_store(config)
    logger = _get_logger(config)

    # Seed a WorkBead for this issue
    bead_data = [{"number": issue, "title": issue_title}]
    _seed_work_beads(store, bead_data, milestone_title or "unknown", None, repo)

    # Dispatch the single issue via the rolling dispatcher.
    # run-issue is for single-issue dispatch (e.g. from GHA); it must not attempt
    # to resume or re-plan the full milestone graph.
    from kiln.dispatch import RollingDispatcher

    dispatcher = RollingDispatcher(config, store, logger)
    asyncio.run(dispatcher.run([issue]))
    console.print(f"[green]Done.[/green] Completed: {dispatcher.completed_count}")


@app.command()
def cost(
    repo: Annotated[str | None, typer.Option()] = None,
    period: Annotated[str, typer.Option(help="today|week|month|all")] = "all",
) -> None:
    """Show LLM cost summary from the local cost ledger."""
    from datetime import UTC, datetime, timedelta

    from kiln.agents.ledger import CostLedger

    ledger = CostLedger()
    run_ids = ledger.all_runs()

    if repo:
        run_id_prefix = repo.replace("/", "-")
        run_ids = [rid for rid in run_ids if rid.startswith(run_id_prefix)]

    if not run_ids:
        console.print("No cost records found.")
        return

    # Determine time cutoff for filtering
    now = datetime.now(UTC)
    cutoff: datetime | None = None
    if period == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        cutoff = now - timedelta(days=7)
    elif period == "month":
        cutoff = now - timedelta(days=30)

    table = Table(title=f"LLM Costs ({period})")
    table.add_column("Run ID")
    table.add_column("Nodes", justify="right")
    table.add_column("Cost (USD)", justify="right")

    grand_total = 0.0
    for run_id in sorted(run_ids):
        summary = ledger.summarize(run_id)
        records = summary["per_node"]
        if cutoff is not None:
            records = [
                r for r in records if r.get("ts") and datetime.fromisoformat(r["ts"]) >= cutoff
            ]
        if not records:
            continue
        total = sum(float(r.get("cost_usd") or 0) for r in records)
        grand_total += total
        table.add_row(run_id, str(len(records)), f"${total:.2f}")

    if not table.rows:
        console.print(f"No cost records found for period '{period}'.")
        return

    table.add_section()
    table.add_row("[bold]TOTAL[/bold]", "", f"[bold]${grand_total:.2f}[/bold]")
    console.print(table)


@app.command()
def report(
    repo: Annotated[str | None, typer.Option()] = None,
    milestone: Annotated[str | None, typer.Option(help="Filter by milestone slug.")] = None,
    all_reports: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    """Show execution report(s) from completed kiln graph runs."""
    from beads.store import BeadStore

    from kiln.config import Config

    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = BeadStore(config.beads_dir, repo)

    reports = store.list_execution_reports(milestone=milestone)
    if not reports:
        console.print("No execution reports found.")
        return

    to_show = reports if all_reports else reports[:1]

    for r in to_show:
        outcome_color = {"success": "green", "partial": "yellow", "failed": "red"}.get(
            r.outcome, "white"
        )
        console.print(
            f"\n[bold]{r.milestone}[/bold]  [{outcome_color}]{r.outcome.upper()}[/{outcome_color}]  "
            f"{r.completed_at.strftime('%Y-%m-%d %H:%M')} UTC  "
            f"cost=${r.total_cost_usd:.4f}"
        )
        if r.plan_confidence is not None:
            console.print(f"  Plan confidence: {r.plan_confidence:.0%}")
        if r.spec_file:
            console.print(f"  Spec: {r.spec_file}")

        if r.modules:
            table = Table(show_header=True, box=None, padding=(0, 2))
            table.add_column("Module")
            table.add_column("State")
            table.add_column("Issue")
            table.add_column("PR")
            table.add_column("Model")
            for m in r.modules:
                state_color = {"done": "green", "abandoned": "red", "pending": "yellow"}.get(
                    m.state, "white"
                )
                table.add_row(
                    m.module,
                    f"[{state_color}]{m.state}[/{state_color}]",
                    f"#{m.issue_number}" if m.issue_number else "-",
                    f"#{m.pr_number}" if m.pr_number else "-",
                    m.model or "-",
                )
            console.print(table)

        if r.empirical_unknowns:
            console.print(f"  [dim]Empirical unknowns ({len(r.empirical_unknowns)}):[/dim]")
            for u in r.empirical_unknowns[:3]:
                console.print(f"    · {u[:80]}")

        if r.risk_flags:
            console.print(f"  [dim]Risk flags:[/dim] {', '.join(r.risk_flags)}")

        counts_str = "  ".join(f"{s}={n}" for s, n in sorted(r.node_counts.items()))
        console.print(f"  [dim]Nodes:[/dim] {counts_str}")


@app.command()
def eval(
    repo: Annotated[str | None, typer.Option()] = None,
    milestone: Annotated[str, typer.Option(help="Milestone slug to evaluate.")] = "",
) -> None:
    """Contrastive eval: compare baseline plan vs research-informed plan.

    Reads the initial plan node and final plan-refine node from the bead store
    (requires a prior dry-run). Shows confidence delta, unknown resolution rate,
    and approach specificity change. No LLM calls — $0 cost.
    """
    from beads.store import BeadStore
    from beads.types import PlanArtifact

    from kiln.config import Config

    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = BeadStore(config.beads_dir, repo)

    nodes = store.list_nodes()
    plan_nodes = [
        n for n in nodes
        if n.type == "plan" and n.state in ("done", "already-done") and n.output
        and (not milestone or n.context.get("milestone", "").startswith(milestone))
    ]

    if not plan_nodes:
        console.print("No completed plan nodes found. Run [bold]kiln run --dry-run[/bold] first.")
        return

    # Sort by completed_at: earliest is baseline, latest is research-informed
    plan_nodes.sort(key=lambda n: n.completed_at or n.created_at)
    baseline_node = plan_nodes[0]
    final_node = plan_nodes[-1]

    if baseline_node.id == final_node.id:
        console.print(
            "[yellow]Only one plan node found — no research was triggered.[/yellow]\n"
            "Research threshold was not met; baseline plan was used directly."
        )
        artifact = PlanArtifact.model_validate(baseline_node.output["artifact"])
        console.print(f"  Confidence: {artifact.confidence:.0%}")
        console.print(f"  Unknowns: {len(artifact.unknowns)}")
        return

    baseline = PlanArtifact.model_validate(baseline_node.output["artifact"])
    final = PlanArtifact.model_validate(final_node.output["artifact"])

    ms = final.milestone or baseline.milestone
    console.print(f"\n[bold]Contrastive Eval: {ms}[/bold]")
    console.print(f"  Baseline node:  {baseline_node.id}")
    console.print(f"  Final node:     {final_node.id}")
    console.print()

    # --- Confidence ---
    conf_delta = final.confidence - baseline.confidence
    conf_color = "green" if conf_delta > 0.02 else ("red" if conf_delta < -0.02 else "yellow")
    console.print(
        f"  Confidence:     {baseline.confidence:.0%} → {final.confidence:.0%}  "
        f"[{conf_color}]({conf_delta:+.0%})[/{conf_color}]"
    )

    # --- Unknowns ---
    initial_unk = set(baseline.unknowns)
    final_unk = set(final.unknowns)
    resolved = initial_unk - final_unk
    new_unk = final_unk - initial_unk
    console.print(
        f"  Unknowns:       {len(initial_unk)} → {len(final_unk)}  "
        f"[green]({len(resolved)} resolved)[/green]"
        + (f"  [yellow](+{len(new_unk)} new)[/yellow]" if new_unk else "")
    )

    # --- Module approach specificity (word count proxy) ---
    modules_changed = 0
    approach_rows = []
    for mod in final.modules:
        base_approach = baseline.module_approaches.get(mod, baseline.approach)
        final_approach = final.module_approaches.get(mod, final.approach)
        base_words = len(base_approach.split())
        final_words = len(final_approach.split())
        changed = base_approach.strip() != final_approach.strip()
        if changed:
            modules_changed += 1
        approach_rows.append((mod, base_words, final_words, changed))

    console.print(
        f"  Approaches:     {modules_changed}/{len(final.modules)} modules changed"
    )
    if approach_rows:
        tbl = Table(show_header=True, box=None, padding=(0, 2))
        tbl.add_column("Module")
        tbl.add_column("Baseline words", justify="right")
        tbl.add_column("Final words", justify="right")
        tbl.add_column("Changed")
        for mod, bw, fw, changed in approach_rows:
            tbl.add_row(
                mod,
                str(bw),
                str(fw),
                "[green]yes[/green]" if changed else "[dim]no[/dim]",
            )
        console.print(tbl)

    # --- Risk flags / new deps ---
    new_flags = set(final.risk_flags) - set(baseline.risk_flags)
    new_deps = set(final.new_dependencies) - set(baseline.new_dependencies)
    if new_flags:
        console.print(f"  New risk flags: {', '.join(new_flags)}")
    if new_deps:
        console.print(f"  New deps:       {', '.join(new_deps)}")

    # --- Verdict ---
    console.print()
    if conf_delta > 0.10 and len(resolved) >= 2:
        verdict = "[bold green]STRONG BENEFIT[/bold green]"
    elif conf_delta > 0.02 or len(resolved) > 0:
        verdict = "[bold yellow]MARGINAL BENEFIT[/bold yellow]"
    elif conf_delta < -0.05:
        verdict = "[bold red]HARMFUL — research reduced confidence[/bold red]"
    else:
        verdict = "[bold red]NO BENEFIT — research changed nothing[/bold red]"

    console.print(f"  Verdict: {verdict}")

    # Rough cost estimate
    n_research = len([n for n in nodes if n.type == "research" and n.state == "done"])
    est_cost = n_research * 0.008 + 0.06  # haiku per group + sonnet refine
    console.print(f"  Est. research overhead: ~${est_cost:.2f}")
    if final.confidence > 0:
        breakeven_pct = (est_cost / 2.0) * 100  # $2 avg failed build cost
        console.print(f"  Break-even CI improvement needed: ~{breakeven_pct:.1f}%")


# ---------------------------------------------------------------------------
# Repo subcommands
# ---------------------------------------------------------------------------


@repo_app.command("add")
def repo_add(
    repo_name: Annotated[str, typer.Argument(help="owner/repo")],
    local_path: Annotated[Path, typer.Option(help="Local clone path.")],
    spec_dir: Annotated[Path | None, typer.Option(help="Spec directory within repo.")] = None,
    default_branch: Annotated[str, typer.Option()] = "mainline",
) -> None:
    """Register a repo in the platform registry."""
    registry = Registry()
    entry = RepoEntry(
        repo=repo_name,
        local_path=local_path.expanduser().resolve(),
        spec_dir=(spec_dir or local_path / "specs").expanduser().resolve(),
        default_branch=default_branch,
    )
    registry.add(entry)
    console.print(f"[green]Registered[/green] {repo_name}")

    # Scaffold labels, CI workflow
    _scaffold_repo(repo_name)
    console.print("  labels and CI workflow scaffolded")

    # Add yeast-bot as collaborator and accept invitation
    _add_bot_collaborator(repo_name)


@repo_app.command("remove")
def repo_remove(
    repo_name: Annotated[str, typer.Argument()],
) -> None:
    """Remove a repo from the registry."""
    registry = Registry()
    if registry.remove(repo_name):
        console.print(f"[green]Removed[/green] {repo_name}")
    else:
        console.print(f"[yellow]Not found:[/yellow] {repo_name}")


@repo_app.command("list")
def repo_list() -> None:
    """List all registered repos."""
    registry = Registry()
    repos = registry.list()
    if not repos:
        console.print("No repos registered. Use: kiln repo add <owner/repo> --local-path <path>")
        return

    table = Table(title="Platform Repo Registry")
    table.add_column("Repo")
    table.add_column("Local Path")
    table.add_column("Spec Dir")
    table.add_column("Branch")

    for entry in repos:
        table.add_row(
            entry.repo,
            str(entry.local_path),
            str(entry.spec_dir),
            entry.default_branch,
        )

    console.print(table)


@app.command()
def reconcile(
    repo: Annotated[
        str | None, typer.Option(help="owner/repo — reconcile one repo. Omit for all.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Reconcile bead state against GitHub reality.

    For each repo in the bead store:
    - Abandoned/running build nodes whose branch was merged → mark done
    - Running nodes with no active process → reset to pending
    - Merged/closed PRs in the merge queue → remove
    - All graph nodes terminal for a milestone → close milestone tracking issue
    - Open PRs whose branch is already on mainline → close as duplicate
    """
    import json as _json
    import re as _re
    import subprocess as _sp

    beads_dir = Path.home() / ".kiln" / "beads"
    if not beads_dir.exists():
        console.print("No bead store found.")
        return

    def _gh(*args: str) -> str:
        r = _sp.run(["gh", *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    def _pr_state(repo: str, pr_number: int) -> str:
        """Returns 'OPEN', 'MERGED', 'CLOSED', or ''."""
        out = _gh("pr", "view", str(pr_number), "--repo", repo, "--json", "state", "--jq", ".state")
        return out.upper()

    def _branch_merged(repo: str, branch: str) -> bool:
        """True if branch has a merged PR or the branch head is on the default branch."""
        out = _gh(
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch,
            "--state",
            "merged",
            "--json",
            "number",
            "--jq",
            "length",
        )
        return out == "1" or int(out or "0") > 0

    def _default_branch(repo: str) -> str:
        out = _gh(
            "repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"
        )
        return out or "mainline"

    def _ms_prefix(node_id: str) -> str:
        m = _re.match(r"^(.*?)(?:-build-|-plan$|-readme$|-research(?:-|$)|-merge$)", node_id)
        return m.group(1) if m else node_id

    repos_to_check: list[tuple[str, Path]] = []
    for owner_dir in sorted(beads_dir.iterdir()):
        if not owner_dir.is_dir():
            continue
        for repo_dir in sorted(owner_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            r = f"{owner_dir.name}/{repo_dir.name}"
            if repo and r != repo:
                continue
            if (repo_dir / "graph").exists():
                repos_to_check.append((r, repo_dir))

    total_fixed = 0

    for r, repo_dir in repos_to_check:
        graph_dir = repo_dir / "graph"
        nodes_raw: list[dict] = []
        for f in graph_dir.glob("*.json"):
            try:
                nodes_raw.append(_json.loads(f.read_text()))
            except Exception:
                continue

        if not nodes_raw:
            continue

        console.print(f"\n[bold]{r}[/bold]")
        fixed = 0

        # --- 1. Reset stale `running` nodes to pending (only if running > 30 min) ---
        now_utc = _dt.now(_datetime.UTC)
        for node in nodes_raw:
            if node.get("state") != "running":
                continue
            started = node.get("started_at") or node.get("created_at", "")
            try:
                started_dt = _dt.fromisoformat(started.replace("Z", "+00:00"))
                age_minutes = (now_utc - started_dt).total_seconds() / 60
            except Exception:
                age_minutes = 999  # unknown age — treat as stale
            if age_minutes < 30:
                console.print(
                    f"  [dim]skip[/dim]    running ({age_minutes:.0f}m < 30m, may be live)  {node['id']}"
                )
                continue
            node_file = graph_dir / f"{node['id']}.json"
            if dry_run:
                console.print(
                    f"  [yellow][dry-run][/yellow] would reset running→pending  {node['id']}  ({age_minutes:.0f}m stale)"
                )
            else:
                node["state"] = "pending"
                node_file.write_text(_json.dumps(node, indent=2))
                console.print(
                    f"  [yellow]reset[/yellow]   running→pending  {node['id']}  ({age_minutes:.0f}m stale)"
                )
            fixed += 1

        # --- 2. Abandoned build/merge nodes — check if branch was merged ---
        for node in nodes_raw:
            if node.get("state") != "abandoned":
                continue
            node_type = node.get("type", "")
            ctx = node.get("context") or {}

            branch = ctx.get("branch")
            # For merge nodes, find branch from the linked build node
            if node_type == "merge" and not branch:
                build_node_id = ctx.get("build_node_id", "")
                build_file = graph_dir / f"{build_node_id}.json"
                if build_file.exists():
                    try:
                        build_node = _json.loads(build_file.read_text())
                        branch = (build_node.get("context") or {}).get("branch")
                    except Exception:
                        pass

            if not branch:
                continue

            merged = _branch_merged(r, branch)
            if not merged:
                continue

            node_file = graph_dir / f"{node['id']}.json"
            if dry_run:
                console.print(
                    f"  [yellow][dry-run][/yellow] would mark done  {node['id']}  (branch {branch} merged)"
                )
            else:
                node["state"] = "done"
                if not isinstance(node.get("output"), dict):
                    node["output"] = {}
                node["output"]["auto_reconciled"] = True
                node["output"]["reconcile_reason"] = f"branch {branch} has merged PR"
                node_file.write_text(_json.dumps(node, indent=2))
                console.print(f"  [green]done[/green]    abandoned→done   {node['id']}  ({branch})")
            fixed += 1

        # --- 3. Merge queue: remove entries for merged/closed PRs ---
        mq_path = repo_dir / "merge-queue.json"
        if mq_path.exists():
            try:
                mq = _json.loads(mq_path.read_text())
                before = len(mq.get("items", []))
                live = []
                for item in mq.get("items", []):
                    state = _pr_state(r, item["pr_number"])
                    if state == "OPEN":
                        live.append(item)
                    else:
                        if dry_run:
                            console.print(
                                f"  [yellow][dry-run][/yellow] would remove PR #{item['pr_number']} from queue [{state}]"
                            )
                        else:
                            console.print(
                                f"  [dim]queue[/dim]   removed PR #{item['pr_number']} [{state}]"
                            )
                if not dry_run and len(live) != before:
                    mq["items"] = live
                    mq_path.write_text(_json.dumps(mq, indent=2))
                    fixed += before - len(live)
            except Exception:
                pass

        # --- Reload nodes after mutations ---
        nodes_raw = []
        for f in graph_dir.glob("*.json"):
            try:
                nodes_raw.append(_json.loads(f.read_text()))
            except Exception:
                continue

        # --- 4. Close milestone tracking issues for complete graphs ---
        # Only use milestone_issue_number from node contexts — never infer from work beads
        milestones: dict[str, list] = {}
        for node in nodes_raw:
            ms = _ms_prefix(node["id"])
            milestones.setdefault(ms, []).append(node)

        terminal = {"done", "wont-do", "failed"}
        for ms, ms_nodes in milestones.items():
            states = {n.get("state") for n in ms_nodes}
            if not states.issubset(terminal):
                continue
            # All terminal — find milestone tracking issue from node contexts only
            ms_issue = None
            for node in ms_nodes:
                ctx = node.get("context") or {}
                if ctx.get("milestone_issue_number"):
                    ms_issue = ctx["milestone_issue_number"]
                    break
            if not ms_issue:
                continue

            # Check if issue is still open
            state_out = _gh(
                "issue", "view", str(ms_issue), "--repo", r, "--json", "state", "--jq", ".state"
            )
            if state_out.lower() != "open":
                continue

            if dry_run:
                console.print(
                    f"  [yellow][dry-run][/yellow] would close issue #{ms_issue} ({ms} — all nodes terminal)"
                )
            else:
                _gh(
                    "issue",
                    "close",
                    str(ms_issue),
                    "--repo",
                    r,
                    "--comment",
                    f"All graph nodes for {ms} are in terminal state. Auto-closed by `kiln reconcile`.",
                )
                console.print(f"  [green]closed[/green]  issue #{ms_issue}  ({ms} complete)")
            fixed += 1

        if fixed == 0:
            console.print("  [dim]nothing to fix[/dim]")
        total_fixed += fixed

    console.print(
        f"\n[bold]reconcile done[/bold] — {total_fixed} fix(es) applied"
        + (" [dry-run]" if dry_run else "")
    )


def _collect_dashboard_rows() -> list[tuple[str, str, list]]:
    """Collect (repo, milestone, nodes) tuples from all bead stores."""
    import json as _json
    import re as _re

    beads_dir = Path.home() / ".kiln" / "beads"
    if not beads_dir.exists():
        return []

    rows: list[tuple[str, str, list]] = []
    for owner_dir in sorted(beads_dir.iterdir()):
        if not owner_dir.is_dir():
            continue
        for repo_dir in sorted(owner_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            graph_dir = repo_dir / "graph"
            if not graph_dir.exists():
                continue

            nodes_by_ms: dict[str, list] = {}
            for f in graph_dir.glob("*.json"):
                try:
                    node = _json.loads(f.read_text())
                except Exception:
                    continue
                nid = node.get("id", "")
                m = _re.match(
                    r"^(.*?)(?:-build-|-plan$|-readme$|-research(?:-|$)|-merge$|-validate$|-bug-)",
                    nid,
                )
                prefix = m.group(1) if m else nid
                nodes_by_ms.setdefault(prefix, []).append(node)

            repo = f"{owner_dir.name}/{repo_dir.name}"
            for ms, nodes in sorted(nodes_by_ms.items()):
                rows.append((repo, ms, nodes))

    return rows


def _milestone_summary(nodes: list) -> tuple[int, int, str, str, str, float]:
    """Return (done, total, bar, status, color, cost_usd) for a milestone's nodes."""
    states = [n.get("state", "") for n in nodes]
    total = len(states)
    done = states.count("done") + states.count("already-done") + states.count("wont-do")
    running = states.count("running")
    pending = states.count("pending")
    abandoned = states.count("abandoned")
    failed = states.count("failed")

    cost_usd: float = 0.0
    for n in nodes:
        out = n.get("output") or {}
        if isinstance(out, dict):
            cost_usd += out.get("cost_usd") or 0.0

    pct = done / total if total else 0.0
    bar_filled = int(pct * 8)
    bar = "█" * bar_filled + "░" * (8 - bar_filled)

    if abandoned or failed:
        parts = []
        if abandoned:
            parts.append(f"{abandoned} abandoned")
        if failed:
            parts.append(f"{failed} failed")
        if running:
            parts.append(f"{running} running")
        if pending:
            parts.append(f"{pending} pending")
        status = "  ".join(parts)
        bar_color = "red"
    elif running:
        parts = [f"{running} running"]
        if pending:
            parts.append(f"{pending} pending")
        status = "  ".join(parts)
        bar_color = "yellow"
    elif pending:
        status = f"{pending} pending"
        bar_color = "dim"
    elif done == total:
        status = "complete"
        bar_color = "green"
    else:
        status = "idle"
        bar_color = "dim"

    return done, total, f"[{bar_color}]{bar}[/{bar_color}]", status, bar_color, cost_usd


def _build_dashboard(show_cost: bool = False) -> Group:
    """Build a static Rich table from bead store (used by --watch)."""
    from rich.text import Text

    rows = _collect_dashboard_rows()
    if not rows:
        return Group(Text("No graphs found.", style="dim"))

    table = Table(title="kiln — all graphs", show_header=True)
    table.add_column("Repo", style="cyan", no_wrap=True)
    table.add_column("Milestone", no_wrap=True)
    table.add_column("Nodes", justify="right", no_wrap=True)
    table.add_column("", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    if show_cost:
        table.add_column("Cost", justify="right", no_wrap=True)

    repo_costs: dict[str, float] = {}
    for repo, ms, nodes in rows:
        done, total, bar_markup, status, _color, cost = _milestone_summary(nodes)
        repo_costs[repo] = repo_costs.get(repo, 0.0) + cost
        row = [repo, ms, f"{done}/{total}", bar_markup, status]
        if show_cost:
            row.append(f"${cost:.2f}" if cost else "")
        table.add_row(*row)

    return Group(table)


def _launch_dashboard_tui(show_cost: bool = False) -> None:
    """Launch the interactive Textual dashboard."""
    import json as _json

    from rich.markup import escape as _escape
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, ScrollableContainer
    from textual.screen import ModalScreen
    from textual.widgets import Footer, Header, Static, Tree
    from textual.widgets.tree import TreeNode

    _STATE_COLOR = {
        "done": "green",
        "already-done": "cyan",
        "running": "cyan",
        "pending": "yellow",
        "abandoned": "red",
        "failed": "red",
    }
    _RETRYABLE = {"abandoned", "failed"}

    def _node_label(node: dict, ms_prefix: str = "") -> str:
        nid = node.get("id", "?")
        display_id = nid[len(ms_prefix):].lstrip("-") if ms_prefix and nid.startswith(ms_prefix) else nid
        state = node.get("state", "?")
        ntype = node.get("type", "")
        color = _STATE_COLOR.get(state, "white")
        out = node.get("output", {}) or {}
        cost_str = ""
        if show_cost and isinstance(out, dict) and out.get("cost_usd"):
            cost_str = f"  [dim]${out['cost_usd']:.2f}[/dim]"
        conf_str = ""
        if isinstance(out, dict) and out.get("confidence") is not None:
            c = out["confidence"]
            conf_color = "green" if c >= 0.8 else ("yellow" if c >= 0.6 else "red")
            conf_str = f"  [{conf_color}]{c:.2f}[/{conf_color}]"
        return f"[{color}]{_escape(state):10}[/{color}]  [{('bold' if ntype == 'build' else 'dim')}]{_escape(display_id)}[/]{conf_str}{cost_str}"

    def _bead_path(repo: str, node_id: str) -> Path:
        parts = repo.split("/", 1)
        return Path.home() / ".kiln" / "beads" / parts[0] / parts[1] / "graph" / f"{node_id}.json"

    class NodeDetailScreen(ModalScreen):
        BINDINGS = [
            Binding("escape", "dismiss", "Close"),
            Binding("q", "dismiss", "Close"),
        ]
        CSS = """
        NodeDetailScreen {
            align: center middle;
        }
        #dialog {
            width: 90%;
            max-height: 80%;
            border: thick $accent;
            background: $surface;
            padding: 1 2;
        }
        #dialog-title {
            text-style: bold;
            color: $accent;
            padding-bottom: 1;
        }
        #dialog-content {
            height: auto;
            max-height: 1fr;
            overflow-y: auto;
        }
        #dialog-footer {
            color: $text-muted;
            padding-top: 1;
        }
        """

        def __init__(self, node: dict, repo: str) -> None:
            super().__init__()
            self._node = node
            self._repo = repo

        def compose(self) -> ComposeResult:
            node = self._node
            nid = node.get("id", "?")
            state = node.get("state", "?")
            ntype = node.get("type", "?")
            color = _STATE_COLOR.get(state, "white")

            header_lines: list[str] = []
            header_lines.append(f"[bold]Repo:[/bold]  {_escape(self._repo)}")
            header_lines.append(f"[bold]ID:[/bold]    {_escape(nid)}")
            header_lines.append(f"[bold]Type:[/bold]  {_escape(ntype)}")
            header_lines.append(f"[bold]State:[/bold] [{color}]{_escape(state)}[/{color}]")

            for ts_key in ("created_at", "started_at", "finished_at"):
                if node.get(ts_key):
                    header_lines.append(f"[bold]{ts_key}:[/bold] {_escape(str(node[ts_key]))}")

            plain_parts: list[str] = []
            ctx = node.get("context") or {}
            if ctx:
                plain_parts.append("Context\n" + _json.dumps(ctx, indent=2))

            out = node.get("output") or {}
            if out:
                plain_parts.append("Output\n" + _json.dumps(out, indent=2))

            retry_hint = ""
            if state in _RETRYABLE:
                retry_hint = "  [bold yellow]x[/bold yellow] Retry"

            yield Container(
                Static(f"Node — {_escape(nid)}", id="dialog-title"),
                ScrollableContainer(
                    Static("\n".join(header_lines)),
                    Static("\n\n".join(plain_parts), markup=False),
                    id="dialog-content",
                ),
                Static(f"[dim]Esc / q — close{retry_hint}[/dim]", id="dialog-footer"),
                id="dialog",
            )

        def action_dismiss(self) -> None:
            self.dismiss()

    class DashboardApp(App):
        TITLE = "kiln dashboard"
        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("space", "toggle_node", "Expand/Collapse", show=True),
            Binding("enter", "toggle_node", "Expand/Collapse", show=False),
            Binding("d", "diagnose", "Diagnose", show=True),
            Binding("x", "retry", "Retry", show=True),
        ]
        CSS = """
        Tree {
            padding: 1 2;
        }
        """

        def compose(self) -> ComposeResult:
            yield Header()
            yield Tree("kiln — all graphs", id="tree")
            yield Footer()

        def on_mount(self) -> None:
            # On first load expand all repo nodes by default
            rows = _collect_dashboard_rows()
            default_expanded = {repo for repo, _, _ in rows}
            self._populate(restore_expanded=default_expanded)
            self.set_interval(30, self.action_refresh)

        def _expanded_keys(self) -> set[str]:
            """Collect plain-text keys of expanded non-leaf nodes."""
            tree: Tree = self.query_one("#tree", Tree)
            expanded: set[str] = set()

            def _walk(node: TreeNode) -> None:
                if node.allow_expand and node._expanded and isinstance(node.data, dict):  # noqa: SLF001
                    key = node.data.get("key")
                    if key:
                        expanded.add(key)
                for child in node.children:
                    _walk(child)

            _walk(tree.root)
            return expanded

        def _populate(self, restore_expanded: set[str] | None = None) -> None:
            tree: Tree = self.query_one("#tree", Tree)
            if restore_expanded is None:
                restore_expanded = self._expanded_keys()
            tree.clear()
            tree.root.expand()

            rows = _collect_dashboard_rows()
            if not rows:
                tree.root.add_leaf("[dim]No graphs found.[/dim]")
                return

            last_repo = None
            repo_node: TreeNode | None = None
            repo_cost: dict[str, float] = {}
            repo_nodes_map: dict[str, TreeNode] = {}

            # First pass: compute per-repo totals
            for repo, _ms, nodes in rows:
                for n in nodes:
                    out = n.get("output") or {}
                    if isinstance(out, dict):
                        repo_cost[repo] = repo_cost.get(repo, 0.0) + (out.get("cost_usd") or 0.0)

            for repo, ms, nodes in rows:
                if repo != last_repo:
                    total_cost = repo_cost.get(repo, 0.0)
                    cost_suffix = f"  [dim]${total_cost:.2f}[/dim]" if (show_cost and total_cost) else ""
                    repo_node = tree.root.add(
                        f"[cyan bold]{repo}[/cyan bold]{cost_suffix}",
                        expand=repo in restore_expanded,
                        data={"key": repo},
                    )
                    repo_nodes_map[repo] = repo_node
                    last_repo = repo

                done, total, bar_markup, status, color, cost = _milestone_summary(nodes)
                ms_key = f"{repo}/{ms}"
                cost_str = f"  [dim]${cost:.2f}[/dim]" if (show_cost and cost) else ""
                ms_label = (
                    f"{bar_markup}  [bold]{ms}[/bold]  "
                    f"[dim]{done}/{total}[/dim]  [{color}]{status}[/{color}]{cost_str}"
                )
                ms_node = repo_node.add(
                    ms_label,
                    expand=ms_key in restore_expanded,
                    data={"key": ms_key},
                )

                # Group nodes by pipeline stage, in order:
                #   plan → research·r1 (+ plan-refine-1) → research·r2 (+ plan-refine-2) → build → merge
                import re as _re2

                def _research_round(nid: str) -> int:
                    m = _re2.search(r"-research-r(\d+)-", nid)
                    if m:
                        return int(m.group(1))
                    # old format: ...-research-...-plan-refine-{N}-...
                    m = _re2.search(r"-research-.*-plan-refine-(\d+)-", nid)
                    if m:
                        return int(m.group(1)) + 1
                    return 1  # old format round 1 or unknown

                def _refine_round(nid: str) -> int:
                    m = _re2.search(r"-plan-refine-(\d+)$", nid)
                    return int(m.group(1)) if m else 1

                def _stage_key(n: dict) -> tuple[int, str]:
                    ntype = n.get("type", "")
                    nid = n.get("id", "")
                    if ntype == "plan" and "refine" not in nid:
                        return (0, "plan")
                    if ntype == "research":
                        r = _research_round(nid)
                        return (1, f"research · r{r}")
                    if ntype == "plan" and "refine" in nid:
                        r = _refine_round(nid)
                        return (1, f"research · r{r}")
                    if ntype in ("build", "readme"):
                        return (2, "build")
                    if ntype == "merge":
                        return (3, "merge")
                    return (4, "other")

                from itertools import groupby as _groupby
                stage_sorted = sorted(nodes, key=lambda n: (_stage_key(n), n.get("id", "")))
                for (_, stage_label), group_iter in _groupby(stage_sorted, key=_stage_key):
                    group_nodes = list(group_iter)
                    stage_key = f"{ms_key}/{stage_label}"
                    grp = ms_node.add(
                        f"[dim]{stage_label}  ({len(group_nodes)})[/dim]",
                        expand=stage_key in restore_expanded or any(
                            n.get("state") in ("running", "failed", "abandoned") for n in group_nodes
                        ),
                        data={"key": stage_key},
                    )
                    for n in group_nodes:
                        grp.add_leaf(_node_label(n, ms_prefix=ms), data={"node": n, "repo": repo})

        def _cursor_node_data(self) -> dict | None:
            tree: Tree = self.query_one("#tree", Tree)
            cursor = tree.cursor_node
            if cursor is not None and not cursor.allow_expand and isinstance(cursor.data, dict):
                return cursor.data
            return None

        def action_refresh(self) -> None:
            self._populate()
            self.notify("Refreshed")

        def action_toggle_node(self) -> None:
            tree: Tree = self.query_one("#tree", Tree)
            node = tree.cursor_node
            if node is not None and node.allow_expand:
                node.toggle()

        def action_diagnose(self) -> None:
            d = self._cursor_node_data()
            if d is None:
                self.notify("Move cursor to a node leaf first", severity="warning")
                return
            self.push_screen(NodeDetailScreen(d["node"], d["repo"]))

        def action_retry(self) -> None:
            d = self._cursor_node_data()
            if d is None:
                self.notify("Move cursor to a node leaf first", severity="warning")
                return
            node = d["node"]
            state = node.get("state", "")
            if state not in _RETRYABLE:
                self.notify(
                    f"Cannot retry — state is '{state}' (only abandoned/failed)", severity="warning"
                )
                return
            nid = node.get("id", "")
            bp = _bead_path(d["repo"], nid)
            if not bp.exists():
                self.notify(f"Bead file not found: {bp}", severity="error")
                return
            try:
                data = _json.loads(bp.read_text())
                data["state"] = "pending"
                data.pop("started_at", None)
                data.pop("finished_at", None)
                if isinstance(data.get("output"), dict):
                    data["output"].pop("error", None)
                bp.write_text(_json.dumps(data, indent=2))
                self.notify(f"[green]Reset {nid} → pending[/green]")
                self._populate()
            except Exception as e:
                self.notify(f"Retry failed: {e}", severity="error")

    DashboardApp().run()


@app.command()
def dashboard(
    watch: Annotated[
        bool, typer.Option("--watch", "-w", help="Non-interactive live refresh every 5s.")
    ] = False,
    show_cost: Annotated[
        bool, typer.Option("--cost", help="Show cost estimates in the dashboard.")
    ] = False,
) -> None:
    """Interactive dashboard — arrow keys to navigate, space/enter to expand nodes."""
    import time as _time

    if watch:
        from rich.live import Live

        with Live(console=console, refresh_per_second=1) as live:
            while True:
                live.update(_build_dashboard(show_cost=show_cost))
                _time.sleep(5)
        return

    _launch_dashboard_tui(show_cost=show_cost)


@app.command()
def drain(
    repo: Annotated[str | None, typer.Option(help="owner/repo to operate on.")] = None,
    watch: Annotated[
        bool, typer.Option("--watch", help="Loop until no PRs remain pending.")
    ] = False,
    interval: Annotated[int, typer.Option(help="Seconds between watch loops.")] = 60,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Drain the merge queue: sync open PRs, merge what's green, skip pending.

    Cleans stale bead queue entries, syncs any open GitHub PRs not yet in the
    queue, then merges PRs where CI passes. Use --watch to loop continuously.
    """
    import json as _json
    import re as _re
    import time as _time

    from beads.types import MergeQueueItem, PRBead

    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)
    logger = _get_logger(config)

    def _gh_json(*args: str) -> list | dict | None:
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        try:
            return _json.loads(r.stdout)
        except _json.JSONDecodeError:
            return None

    def _pr_ci_status(pr_number: int) -> str:
        """Returns 'green', 'pending', 'failing', or 'unknown'."""
        r = subprocess.run(
            [
                "gh",
                "pr",
                "checks",
                str(pr_number),
                "--repo",
                repo,
                "--json",
                "name,state,conclusion",
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return "unknown"
        try:
            checks = _json.loads(r.stdout)
        except _json.JSONDecodeError:
            return "unknown"
        if not checks:
            return "green"
        states = [c.get("state", "") for c in checks]
        conclusions = [c.get("conclusion", "") for c in checks]
        if any(s in ("IN_PROGRESS", "QUEUED", "REQUESTED") for s in states):
            return "pending"
        if all(c in ("SUCCESS", "SKIPPED", "") for c in conclusions):
            return "green"
        return "failing"

    def sync_and_drain() -> tuple[int, int, int]:
        """Returns (merged, pending, failing)."""
        # 1. Remove stale entries (PRs that are already merged/closed)
        queue = store.read_merge_queue()
        live_items = []
        removed = 0
        for item in queue.items:
            r = subprocess.run(
                [
                    "gh",
                    "pr",
                    "view",
                    str(item.pr_number),
                    "--repo",
                    repo,
                    "--json",
                    "state",
                    "--jq",
                    ".state",
                ],
                capture_output=True,
                text=True,
            )
            state = r.stdout.strip().lower()
            if state == "open":
                live_items.append(item)
            else:
                removed += 1
        if removed:
            queue.items = live_items
            store.write_merge_queue(queue)
            console.print(f"  [dim]cleaned {removed} stale queue entries[/dim]")

        # 2. Sync open PRs from GitHub that aren't in the queue
        open_prs = (
            _gh_json(
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--json",
                "number,headRefName,title",
            )
            or []
        )
        queued_prs = {item.pr_number for item in queue.items}
        added = 0
        for pr in open_prs:
            pr_num = pr["number"]
            if pr_num not in queued_prs:
                pr_body = _gh_json("pr", "view", str(pr_num), "--repo", repo, "--json", "body")
                issue_number = 0
                if pr_body:
                    m = _re.search(r"[Cc]loses\s+#(\d+)", pr_body.get("body", ""))
                    if m:
                        issue_number = int(m.group(1))

                queue.items.append(
                    MergeQueueItem(
                        pr_number=pr_num,
                        issue_number=issue_number,
                        branch=pr["headRefName"],
                    )
                )
                if not store.read_pr_bead(pr_num):
                    store.write_pr_bead(
                        PRBead(
                            pr_number=pr_num,
                            repo=repo,
                            issue_number=issue_number,
                            branch=pr["headRefName"],
                        )
                    )
                added += 1
        if added:
            store.write_merge_queue(queue)
            console.print(f"  [dim]synced {added} open PRs into queue[/dim]")

        # 3. Drain
        merged = pending = failing = 0
        queue = store.read_merge_queue()
        for item in list(queue.items):
            ci = _pr_ci_status(item.pr_number)
            branch = item.branch

            if ci == "green":
                if dry_run:
                    console.print(
                        f"  [yellow][dry-run][/yellow] would merge PR #{item.pr_number} ({branch})"
                    )
                    merged += 1
                    continue
                r = subprocess.run(
                    [
                        "gh",
                        "pr",
                        "merge",
                        str(item.pr_number),
                        "--repo",
                        repo,
                        "--squash",
                        "--delete-branch",
                    ],
                    capture_output=True,
                    text=True,
                )
                if r.returncode == 0:
                    console.print(f"  [green]merged[/green] PR #{item.pr_number} ({branch})")
                    pr_bead = store.read_pr_bead(item.pr_number)
                    if pr_bead:
                        pr_bead.state = "merged"  # type: ignore[assignment]
                        store.write_pr_bead(pr_bead)
                    if item.issue_number:
                        wb = store.read_work_bead(item.issue_number)
                        if wb:
                            wb.state = "closed"  # type: ignore[assignment]
                            store.write_work_bead(wb)
                    queue.items = [i for i in queue.items if i.pr_number != item.pr_number]
                    store.write_merge_queue(queue)
                    if logger:
                        logger.merge(item.pr_number, item.issue_number, branch)
                    merged += 1
                else:
                    console.print(
                        f"  [red]merge failed[/red] PR #{item.pr_number}: {r.stderr[:120]}"
                    )
                    failing += 1
            elif ci == "pending":
                console.print(
                    f"  [yellow]pending[/yellow]  PR #{item.pr_number} ({branch}) — CI running"
                )
                pending += 1
            elif ci == "failing":
                console.print(f"  [red]failing[/red]   PR #{item.pr_number} ({branch}) — CI failed")
                failing += 1
            else:
                console.print(
                    f"  [dim]unknown[/dim]   PR #{item.pr_number} ({branch}) — no CI data"
                )
                pending += 1

        return merged, pending, failing

    loop = 0
    while True:
        loop += 1
        console.print(f"\n[bold]drain loop #{loop}[/bold] — {repo}")
        merged, pending, failing = sync_and_drain()
        console.print(f"  [bold]merged {merged}[/bold]  pending {pending}  failing {failing}")

        if not watch or pending == 0:
            break

        console.print(f"  sleeping {interval}s…")
        _time.sleep(interval)


@app.command("gha-dispatch")
def gha_dispatch(
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Dispatch a kiln run from a GitHub Actions issues event.

    Reads GITHUB_EVENT_NAME, GITHUB_EVENT_PATH, and GITHUB_REPOSITORY from
    the environment (set automatically by GitHub Actions). Triggers the graph
    executor when an issue is labeled with 'stage/impl' and has a milestone
    whose spec file exists under specs/.
    """
    import json
    import os

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not repo:
        console.print("[red]error:[/red] GITHUB_REPOSITORY not set")
        raise typer.Exit(1)

    if event_name != "issues":
        console.print(f"[yellow]skip:[/yellow] event {event_name!r} is not 'issues'")
        return

    event: dict = {}
    if event_path and Path(event_path).exists():
        try:
            event = json.loads(Path(event_path).read_text())
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[red]error:[/red] could not parse event payload: {e}")
            raise typer.Exit(1) from e

    action = event.get("action", "")
    label_name = (event.get("label") or {}).get("name", "")

    if action != "labeled" or label_name != "stage/impl":
        console.print(f"[yellow]skip:[/yellow] action={action!r} label={label_name!r}")
        return

    issue = event.get("issue") or {}
    milestone_obj = issue.get("milestone") or {}
    milestone = milestone_obj.get("title", "")
    if not milestone:
        console.print("[yellow]skip:[/yellow] issue has no milestone")
        return

    # Find spec file for this milestone under specs/
    specs_dir = Path("specs")
    spec_file: Path | None = None
    if specs_dir.exists():
        for candidate in sorted(specs_dir.glob("*.md")):
            stem = candidate.stem
            # Match by milestone slug: v0.2.0 matches v0-2-0 or v0.2.0 in filename
            normalized = milestone.replace(".", "-").lower()
            if normalized in stem.lower() or milestone.lower() in stem.lower():
                spec_file = candidate
                break

    if not spec_file:
        console.print(f"[yellow]skip:[/yellow] no spec found for milestone {milestone!r} in specs/")
        return

    console.print(f"GHA dispatch: repo={repo} milestone={milestone} spec={spec_file}")

    if dry_run:
        console.print("[yellow][dry-run][/yellow] would dispatch graph executor")
        return

    config = Config.from_env(repo)
    store = _get_store(config)
    logger = _get_logger(config)

    issue_numbers = _run_single_spec(
        spec_file, repo, config, store, logger, milestone, dry_run=False
    )

    if not issue_numbers:
        console.print("No issues to dispatch.")
        return

    from kiln.graph.builder import build_greenfield_graph
    from kiln.graph.executor import GraphExecutor, make_handlers

    handlers = make_handlers(store=store, logger=logger)
    executor = GraphExecutor(
        config=config,
        handlers=handlers,
        store=store,
        logger=logger,
        concurrency=config.concurrency,
        watchdog_interval=float(config.watchdog_interval_seconds),
    )

    graph = build_greenfield_graph(
        milestone=milestone,
        spec_file=spec_file,
        repo=repo,
        repo_local_path=str(Path.cwd()),
        milestone_issue_number=issue_numbers[0] if issue_numbers else None,
    )

    result = asyncio.run(executor.run(graph))
    console.print(
        f"GHA dispatch done: done={len(result.done)} failed={len(result.failed)} "
        f"abandoned={len(result.abandoned)}"
    )
    if result.failed or result.abandoned:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Graph subcommands
# ---------------------------------------------------------------------------

_NODE_STATE_COLORS = {
    "pending": "dim",
    "running": "yellow",
    "done": "green",
    "already-done": "cyan",
    "failed": "red",
    "abandoned": "red",
    "wont-do": "dim",
}


@graph_app.command("nodes")
def graph_nodes(
    repo: Annotated[str | None, typer.Option(help="owner/repo to operate on.")] = None,
    milestone: Annotated[str | None, typer.Option(help="Filter by milestone prefix.")] = None,
    state: Annotated[
        str | None, typer.Option(help="Filter by state: pending|running|done|failed|abandoned.")
    ] = None,
) -> None:
    """List graph execution nodes for a repo."""
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    all_nodes = store.list_nodes()
    nodes = all_nodes
    if milestone:
        nodes = [n for n in nodes if n.id.startswith(f"{milestone}-")]
    if state:
        nodes = [n for n in nodes if n.state == state]

    if not nodes:
        console.print("No graph nodes found.")
        return

    table = Table(title=f"Graph Nodes — {repo}" + (f" / {milestone}" if milestone else ""))
    table.add_column("Node ID")
    table.add_column("Type")
    table.add_column("State")
    table.add_column("Model")
    table.add_column("Retries", justify="right")
    table.add_column("Cost (USD)", justify="right")

    _TYPE_ORDER = {"plan": 0, "research": 1, "build": 2, "merge": 3, "readme": 4}
    for node in sorted(nodes, key=lambda n: (_TYPE_ORDER.get(n.type, 5), n.id)):
        color = _NODE_STATE_COLORS.get(node.state, "white")
        node_model = (node.output or {}).get("model") or node.assigned_model or ""
        cost = (node.output or {}).get("cost_usd")
        cost_str = f"${cost:.2f}" if cost is not None else ""
        table.add_row(
            node.id,
            node.type,
            f"[{color}]{node.state}[/{color}]",
            node_model,
            str(node.retry_count) if node.retry_count else "0",
            cost_str,
        )

    console.print(table)
    console.print(f"\n[dim]{len(nodes)} node(s)[/dim]")


@graph_app.command("node")
def graph_node(
    node_id: Annotated[str, typer.Argument(help="Node ID to inspect.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo to operate on.")] = None,
) -> None:
    """Show full details of a single graph node."""
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    node = store.read_node(node_id)
    if node is None:
        console.print(f"[red]error:[/red] node '{node_id}' not found")
        raise typer.Exit(1)

    color = _NODE_STATE_COLORS.get(node.state, "white")
    console.print(f"\n[bold]{node.id}[/bold]")
    console.print(f"  type:       {node.type}")
    console.print(f"  state:      [{color}]{node.state}[/{color}]")
    console.print(f"  retries:    {node.retry_count} / {node.max_retries}")
    if node.assigned_model:
        console.print(f"  model:      {node.assigned_model}")
    if node.depends_on:
        console.print(f"  depends_on: {', '.join(node.depends_on)}")
    if node.started_at:
        console.print(f"  started:    {node.started_at.isoformat()}")
    if node.completed_at:
        console.print(f"  completed:  {node.completed_at.isoformat()}")
    if node.context:
        console.print("\n[bold]Context:[/bold]")
        for k, v in node.context.items():
            console.print(f"  {k}: {v}")
    if node.output:
        console.print("\n[bold]Output:[/bold]")
        for k, v in node.output.items():
            if k not in ("artifact",):  # skip large nested objects
                console.print(f"  {k}: {v}")
        if "artifact" in node.output:
            console.print("  artifact: [dim](present — use --json to see full)[/dim]")


@graph_app.command("retry")
def graph_retry(
    node_id: Annotated[str, typer.Argument(help="Node ID to retry.")],
    repo: Annotated[str | None, typer.Option(help="owner/repo to operate on.")] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Reset even if node is not failed/abandoned.")
    ] = False,
) -> None:
    """Reset a failed or abandoned node to pending so it can be re-executed."""
    repo = _require_repo(repo)
    config = Config.from_env(repo)
    store = _get_store(config)

    node = store.read_node(node_id)
    if node is None:
        console.print(f"[red]error:[/red] node '{node_id}' not found")
        raise typer.Exit(1)

    if node.state not in ("failed", "abandoned") and not force:
        console.print(
            f"[yellow]warning:[/yellow] node '{node_id}' is in state '{node.state}' "
            "(not failed/abandoned). Use --force to reset anyway."
        )
        raise typer.Exit(1)

    old_state = node.state
    node.state = "pending"
    node.started_at = None
    node.completed_at = None
    store.write_node(node)
    console.print(f"[green]ok[/green] node '{node_id}': {old_state} → pending")


if __name__ == "__main__":
    app()
