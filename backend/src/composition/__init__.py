"""
Composition Root.

Centralized dependency wiring for the application.
All factory functions that instantiate adapters belong here to maintain
hexagonal architecture (domain NEVER imports from adapters).
"""

from src.adapters.gemini_adapter import GeminiAdapter
from src.agents.voice_orchestrator import VoiceOrchestrator
from src.domain.services.evaluation_service import EvaluationService
from src.domain.services.hint_service import HintService

# Module-level adapter instance for sharing between services
_gemini_adapter: GeminiAdapter | None = None


def _get_gemini_adapter() -> GeminiAdapter:
    """Get or create shared Gemini adapter instance."""
    global _gemini_adapter
    if _gemini_adapter is None:
        _gemini_adapter = GeminiAdapter()
    return _gemini_adapter


def create_hint_service() -> HintService:
    """Create HintService with Gemini adapter.

    Returns:
        HintService configured with GeminiAdapter
    """
    adapter = _get_gemini_adapter()
    return HintService(adapter)


def create_evaluation_service() -> EvaluationService:
    """Create EvaluationService with Gemini adapter.

    This factory function belongs in the composition root (not domain layer)
    because it instantiates a concrete adapter.

    Returns:
        EvaluationService configured with GeminiAdapter
    """
    adapter = _get_gemini_adapter()
    return EvaluationService(adapter)


def create_voice_orchestrator(checkpointer=None) -> VoiceOrchestrator:
    """Create VoiceOrchestrator with dependencies.

    Args:
        checkpointer: Optional LangGraph checkpointer for crash recovery.
            Use AsyncSqliteSaver for production.

    Returns:
        VoiceOrchestrator configured with EvaluationService
    """
    evaluation_service = create_evaluation_service()
    return VoiceOrchestrator(evaluation_service, checkpointer=checkpointer)
