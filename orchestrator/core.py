"""
Orchestrateur principal — délègue à llm_core (orchestration LLM-driven).

Pour revenir au pipeline déterministe, remplacer l'import par :
    from orchestrator._pipeline_core import plan_roadtrip  # noqa: F401
"""
from orchestrator.llm_core import plan_roadtrip  # noqa: F401
