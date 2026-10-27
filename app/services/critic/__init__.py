"""Independent critics that review and improve outputs before the user sees them.

``EvaluatorOptimizer`` is the reusable critique → revise loop; ``DraftCritic`` applies
it to email drafts, with deterministic checks (placeholders, missing parts, guessed
recipients) ahead of an optional model review.
"""

from app.services.critic.draft import (
    EMAIL_RE,
    DraftCritic,
    EmailDraft,
    confirmed_recipients,
    fill_signature,
    structural_issues,
    valid_recipients,
)
from app.services.critic.loop import Critique, EvaluatorOptimizer, ReviewedOutput

__all__ = [
    "EMAIL_RE",
    "Critique",
    "DraftCritic",
    "EmailDraft",
    "EvaluatorOptimizer",
    "ReviewedOutput",
    "confirmed_recipients",
    "fill_signature",
    "structural_issues",
    "valid_recipients",
]
