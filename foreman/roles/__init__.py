"""The five agents (CLAUDE.md section 6): Orchestrator, Fleet, Navigation, Safety, Recovery.

They are plain Python modules. Each has one job and calls fixed tools, so the
plans are fast and never break a rule. No LLM is needed for them to work.
"""

from foreman.roles.orchestrator import ROUTES, Orchestrator

__all__ = ["ROUTES", "Orchestrator"]
