"""Agent prompt templates — build, readme, research, and plan prompts."""

from __future__ import annotations

from pathlib import Path


def _load_standards(*names: str) -> str:
    """Load named standards files from the standards/ directory next to this package."""
    standards_dir = Path(__file__).parent.parent.parent.parent / "standards"
    if not standards_dir.exists():
        return ""
    parts = []
    for name in names:
        path = standards_dir / f"{name}.md"
        if path.exists():
            parts.append(path.read_text())
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Build agent prompt (ported from runner.py)
# ---------------------------------------------------------------------------


def build_agent_prompt(
    issue_number: int,
    issue_title: str,
    issue_body: str,
    branch: str,
    repo: str,
    allowed_scope: list[str] | None = None,
    workspace_ready: bool = False,
) -> str:
    """Build the standard sub-agent prompt for impl work."""
    if workspace_ready:
        scope_lines = ""
        if allowed_scope:
            file_list = "\n".join(f"  - {f}" for f in allowed_scope)
            scope_lines = f"""
SCOPE ENFORCEMENT IS ACTIVE. A pre-commit hook will REJECT any commit that modifies
files outside the allowed list. Do not use --no-verify or --no-gpg-sign to bypass it.

Allowed files (create or modify ONLY these):
{file_list}

If you need a file not on this list, check whether it belongs to another module.
Do not modify pyproject.toml, CLAUDE.md, or README.md unless they are on the list above.
"""
        setup_step = f"""\
1. The repo is already cloned in the current directory and branch `{branch}` is already
   created and pushed to origin. Do NOT run `git clone` or `git checkout -b`.
   Verify you are on the right branch: `git branch --show-current` (should print `{branch}`)."""
    else:
        scope_lines = ""
        if allowed_scope:
            scope_lines = f"\nAllowed scope (only modify files within): {', '.join(allowed_scope)}"
        setup_step = f"""\
1. Clone the repo and create your branch:
   ```
   gh repo clone {repo} .
   git checkout -b {branch}
   git push -u origin {branch}
   ```"""

    standards = _load_standards("code", "tests", "commits", "prs")
    standards_block = (
        f"\n\n---\n\n## kiln Agent Standards\n\n{standards}\n\n---\n" if standards else ""
    )

    return f"""You are implementing GitHub issue #{issue_number} in repo `{repo}` on branch `{branch}`.
{standards_block}
Issue: {issue_title}

{issue_body}
{scope_lines}
Steps:
{setup_step}
2. Read the full issue: `gh issue view {issue_number} --repo {repo}`
3. Before writing any code, reason through the approach, identify constraints, and plan the implementation.
4. Implement the changes.{" Only modify files within: " + ", ".join(allowed_scope) if allowed_scope and not workspace_ready else ""}
5. Write comprehensive tests for every file you create or modify. For each module:
   - Unit test every public function and class method with meaningful inputs
   - Cover edge cases, error paths, and boundary conditions
   - Do not write trivial smoke tests that only check a function runs without error
   - Aim for full branch coverage of the logic you implement
6. Run the FULL test suite — not just your new tests:
   ```
   uv run pytest
   ```
   All tests must pass. If existing tests fail:
   - Check if they were already failing before your changes: `git stash && uv run pytest && git stash pop`
   - If pre-existing: note it in the PR description and file a follow-up issue, but do NOT leave your own
     changes in a state that makes it worse
   - If your changes caused the regression: fix it before pushing
7. Run lint on the FULL source tree — not just your new files:
   ```
   uv run ruff check
   uv run ruff format --check
   ```
8. Commit referencing the issue: `git commit -m "feat: <description> (closes #{issue_number})"`
9. `git push`
10. Create PR: `gh pr create --repo {repo} --title "<title>" --body "Closes #{issue_number}"`
11. Watch CI: `gh pr checks <PR-number> --watch`
12. Read feedback: `gh pr view <PR-number> --json reviews,comments`
    Inline comments: `gh api repos/{repo}/pulls/<PR-number>/comments`
13. Triage feedback:
    - Fix now: in scope and clear → fix, push, re-check
    - File issue: valid but out of scope → `gh issue create --repo {repo}`
    - Skip: false positive → note in PR comment
14. STOP. Do not merge.

IMPORTANT — if you find that some or all of the work described in this issue has already
been implemented by a previous PR, you MUST leave a comment on the issue explaining:
- Which parts were already done and by which PR(s) (check `gh pr list --repo {repo} --state merged`)
- Which parts (if any) remained and what you implemented
- Why the diff is small if that is the case

Example comment:
  `gh issue comment {issue_number} --repo {repo} --body "Most of this was already implemented in PR #X (module-foo) and PR #Y (module-bar). The remaining gap was [describe]. This PR adds [describe]."`

Do this BEFORE creating the PR so the context is visible on the issue."""


# ---------------------------------------------------------------------------
# Readme agent prompt
# ---------------------------------------------------------------------------


def readme_agent_prompt(repo: str, milestone: str, plan_artifact: dict) -> str:
    """Build the sub-agent prompt for writing a milestone README."""
    approach = plan_artifact.get("approach", "")
    modules = plan_artifact.get("modules", [])
    files_per_module = plan_artifact.get("files_per_module", {})

    module_lines = []
    for mod in modules:
        files = files_per_module.get(mod, [])
        module_lines.append(f"- **{mod}**: {', '.join(files)}")
    modules_text = "\n".join(module_lines)

    standards = _load_standards("commits", "prs")
    standards_block = (
        f"\n\n---\n\n## kiln Agent Standards\n\n{standards}\n\n---\n" if standards else ""
    )

    return f"""You are writing the README.md for the GitHub repo `{repo}` after a completed implementation milestone: `{milestone}`.
{standards_block}
Implementation summary:
{approach}

Modules and files:
{modules_text}

Steps:
1. Clone the repo: `gh repo clone {repo} .`
2. Read the existing source files to understand what was built.
3. Write a clear, concise README.md at the repo root. Include:
   - Project name and one-line description
   - What it does (2-3 sentences)
   - How to install / run (based on pyproject.toml if present)
   - Module overview (one line per module)
   - How to run tests
4. Create a branch: `git checkout -b docs/readme`
5. `git add README.md && git commit -m "docs: add README"`
6. `git push -u origin docs/readme`
7. `gh pr create --repo {repo} --title "docs: add README" --body "Auto-generated README for {milestone}"`
8. Wait for CI: `gh pr checks <PR-number> --watch --repo {repo}`
9. Squash merge: `gh pr merge <PR-number> --repo {repo} --squash --delete-branch`
"""


# ---------------------------------------------------------------------------
# Research prompt
# ---------------------------------------------------------------------------

RESEARCH_PROMPT = """You are a research agent. Your job is to **close** the questions below —
produce definitive answers a planning agent can act on without further research.

**Non-negotiable rules:**
- Answer every question in the list. Do not skip any.
- Your job is to resolve questions, not generate new ones. Do not surface new sub-questions
  or angles that "would need further investigation". If you cannot find a definitive answer,
  give your best assessment and state the residual uncertainty clearly.
- Output your complete report directly to stdout. Do NOT write files to disk.
- Do NOT ask for permission, clarification, or confirmation. Work autonomously to completion.

Repo: {repo}
Milestone: {milestone}
Research group: {research_group}
{sibling_groups_block}
Questions to resolve:
{unknowns}
{prior_findings_block}
---

## Step 1 — Assess each question

Before researching, briefly assess each question:
- **Flawed premise**: if the question assumes something false, state it explicitly and answer
  the real underlying concern instead.
- **Non-issue**: if the concern is quickly shown to be irrelevant, say so directly and move on.
  "This is not a concern because X" is a complete and high-value answer.
- Otherwise: proceed to investigate.

## Step 2 — Investigate

**Codebase questions** (existing patterns, interfaces, schemas):
{repo_clone_instruction}
Label all findings from this repo as **[internal]** — they are not independent corroboration.

**External questions** (APIs, libraries, frameworks, platform behavior):
Use WebSearch/WebFetch. Prefer primary sources (official docs, source code, changelogs).
Cite the URL for every external claim.

**Bias toward action**: fetch sources rather than reasoning about whether they exist.
A failed fetch is more informative than a guess.

Investigate all questions in this group together — shared context between related questions
is an advantage. Evidence that resolves one question may directly inform another.

**Prior art — investigate when building something new:** If the questions concern a capability
that needs to be built (not just understanding an existing interface), search for existing
solutions: open-source libraries, similar projects, published techniques, forum analyses,
reverse-engineering write-ups, or reference implementations. The goal is to understand how
others have solved the same problem — a well-understood prior approach is always preferable
to designing from scratch. If the questions are purely about understanding an existing
codebase interface, skip prior art for that group.

## Step 3 — Report

Write one section per question, then a prior art section, then a shared confidence section.

For each question, mark each finding as one of:
- **[CONFIRMED]** — directly verified from a primary source (source code, official docs,
  live API response). No further validation needed.
- **[PROVISIONAL]** — strongly inferred but not directly observed; would require live
  testing or a running instance to fully confirm. State what test would confirm it.

### Q: [restate the question, corrected if premise was flawed]

**Finding:** [CONFIRMED] or [PROVISIONAL] — what you found and where (URL or [internal] path).

**Recommendation:** One concrete, actionable sentence for the planning agent.

---

After all questions, add (only if the group concerns something being built, not just
understanding an existing interface):

### Prior Art

Survey the space: existing libraries, similar projects, published techniques, forum
discussions, or reverse-engineering analyses relevant to this group. For each: name or
source URL, maturity/credibility, and a one-line verdict:
- **use as dependency** — drop-in solution, adopt now
- **deferred upgrade** — better than current approach but out of scope for this milestone;
  name the specific milestone where this should be revisited
- **use as reference** — not a direct dependency, but the implementation technique is worth adopting
- **not suitable** — explain why

If nothing relevant exists, say so explicitly. Skip this section entirely if the group's
questions were purely about reading existing internal interfaces.

---

Then:

### Confidence

Rate confidence per key claim across all questions:
- **[claim]**: 0.X — reason

For any claim rated above 0.85, note whether your sources are **independently primary**
(different original sources) or **circularly cited** (multiple sites all tracing to the
same original post/doc). Circular citation does not strengthen confidence — treat it as
a single source.

**Overall confidence: 0.X** — single float, weakest critical claim across all questions.
Write this line even if all questions were dismissed as false premises — in that case,
rate your confidence that the premises are false (typically 0.90+).
"""


# ---------------------------------------------------------------------------
# Plan prompt
# ---------------------------------------------------------------------------

PLAN_PROMPT = """You are a planning agent. Read the spec and codebase context below,
then produce a structured implementation plan as JSON.

The spec may be minimal (title + free text only) or fully structured. Either is valid.
If sections are absent, infer reasonable structure from the description.

Spec:
{spec_text}

Codebase context:
{codebase_context}

Research findings (if any):
{research_findings}

{prior_plan}

---

Produce a JSON object exactly matching this schema (no markdown fences):
{{
  "milestone": "<version slug — e.g. 'v0.1.0' or 'auth-refactor'; NOT the full title>",
  "modules": ["<module1>", "<module2>"],
  "files_per_module": {{
    "<module1>": ["<path/to/file.py>", "tests/test_<module1>.py"],
    "<module2>": ["<path/to/file.py>", "tests/test_<module2>.py"]
  }},
  "approach": "<overall strategy — enough context for build agents to understand how modules fit together>",
  "module_approaches": {{
    "<module1>": "<what this ticket must implement, scoped to its files only>",
    "<module2>": "<what this ticket must implement, scoped to its files only>"
  }},
  "confidence": <0.0–1.0>,
  "unknowns": ["<single-concern question>", ...],
  "risk_flags": ["<flag>", ...],
  "module_dependencies": {{
    "<module-that-must-build-last>": ["<module-it-depends-on>", ...]
  }},
  "new_dependencies": ["<package-spec>", ...],
  "research_groups": {{
    "<group-slug>": ["<unknown>", ...]
  }},
  "empirical_unknowns": ["<question that requires running code or a live system to answer>", ...]
}}

---

## modules and files_per_module

Each module becomes an isolated PR. Build agents work from a single branch, can only touch
their scoped files, and cannot coordinate with other agents mid-build. Design modules so that
each one can be fully implemented, tested, and merged without depending on in-progress work
in another module.

- One module per coherent responsibility boundary. Do not split a class hierarchy across
  modules or merge unrelated concerns into one.
- files_per_module must list actual files to create or modify — not directories.
- Each module's file list MUST include its test file(s). Agents are scoped to their file
  list; omitting tests means no tests get written for that module.

## module_approaches

Write the approach for each module as if handing it to an agent who will only see that text
and their file list — no other context. It must be specific enough to implement from.
"Implement the storage layer" is not sufficient. "Implement SqliteBeadStore with write(),
read(), and delete() methods; CREATE TABLE migration runs at startup" is.

**Research findings must be reflected in module approaches.** If research findings or
risk_flags identify a correction to what a module approach says (wrong method name, wrong
API, wrong DB pattern, wrong file path, constraint the agent must know), the correction
MUST appear in the module approach itself — not only in risk_flags. Build agents read only
their module approach; they will never see risk_flags or research findings.

**Empirical unknowns that affect a module must appear in that module's approach.** If an
empirical unknown (e.g., "TCC permission silently blocks X in CI") directly constrains how
a module must be implemented or tested, state the constraint explicitly in the approach.
Example: "All Quartz API calls must be mocked in tests — do not make live macOS API calls."

## confidence and unknowns

Report your honest confidence that the plan is complete and correct as specified. Low
confidence means something external is unresolved — not that you need more thinking time.

**Two kinds of unknowns — classify them correctly:**

`unknowns` — web-researchable questions. These will trigger a research pass and must be
answerable by reading documentation, source code, or third-party analysis. Examples:
- Does this API exist and what are its parameters?
- Does library X support Python 3.12?
- What does Warden actually scan for on macOS?
- Does ScreenCaptureKit require TCC permission for a compiled binary?

`empirical_unknowns` — questions that require running code, a live system, or an installed
application to answer. No amount of web research will close them. Examples:
- What are the exact pixel coordinates of UI element X at resolution Y?
- Does OCR library X achieve N% accuracy on this application's specific font?
- What is the actual frame latency of this capture API against this game?
- Does this authentication flow behave as documented when actually run?

Empirical unknowns do NOT generate research nodes. They become test stubs: the build agent
writes a test that validates the assumption against the live system. Set confidence
reflecting only the web-researchable uncertainties — empirical unknowns are expected and
do not lower confidence.

When listing `unknowns` (web-researchable):
- Each item must address a single, answerable external question.
- Do not list implementation choices — those are decisions, not unknowns.
- Do not list things already answerable from the codebase context or spec provided.

## research_groups

Group unknowns by domain so findings are organized semantically. Use short slugs
(e.g. "screen_capture", "anti_cheat", "ocr"). Omit this field entirely if unknowns is empty.

On plan-refine runs (when a previous plan is provided in the prompt), revisit your group
definitions — do not blindly copy the prior round's groups. Prior research may have resolved
some questions entirely, revealed new domains, or shown that two groups should merge. Regroup
based on remaining unknowns and what you learned.

## module_dependencies

List dependencies only where a module genuinely cannot pass CI without another module's code
being on mainline first — shared runtime types, imported interfaces, or infrastructure files
(pyproject.toml, etc.) are common causes. Omit the key for modules with no dependencies;
do not emit empty lists.

## risk_flags

Include any flags that affect how the plan should be reviewed. Examples: "security" (auth,
crypto, secrets handling), "novel-domain" (unfamiliar tech stack), "multi-module-coordination"
(modules sharing mutable state). These are examples — use your own judgment about what flags
are relevant.

## new_dependencies

List third-party packages not already in pyproject.toml. Use PEP 508 specifiers (e.g.
"anthropic>=0.40"). For git-hosted packages: "pkg @ git+https://github.com/...". Omit if empty.
"""
