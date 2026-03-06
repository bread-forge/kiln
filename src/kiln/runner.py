"""runner.py — shim for backward compatibility.

RunResult and run_agent now live in kiln.agents.runner.
build_agent_prompt now lives in kiln.agents.prompts.
"""

from kiln.agents.prompts import build_agent_prompt  # noqa: F401
from kiln.agents.runner import RunResult, run_agent  # noqa: F401
