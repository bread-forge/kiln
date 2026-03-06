"""forge.py — shim for backward compatibility.

spec_forge and helpers now live in kiln.forge (the sub-package).
"""

from kiln.forge.interview import _apply_interview, _run_interview  # noqa: F401
from kiln.forge.main import spec_forge  # noqa: F401
from kiln.forge.validator import _check_violations  # noqa: F401
