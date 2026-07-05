"""Business rules for guardrails validation."""

from dataclasses import dataclass
from enum import Enum


class RuleType(Enum):
    """Types of validation rules."""

    TOXICITY = "toxicity"
    PII_LEAK = "pii_leak"
    HALLUCINATION = "hallucination"
    BUSINESS_POLICY = "business_policy"
    PROFANITY = "profanity"


@dataclass
class GuardrailRule:
    """Definition of a guardrail rule."""

    name: str
    rule_type: RuleType
    description: str
    is_critical: bool = True
    enabled: bool = True


# Default guardrail rules
DEFAULT_RULES = [
    GuardrailRule(
        name="no_toxic_language",
        rule_type=RuleType.TOXICITY,
        description="Response must not contain toxic or offensive language",
        is_critical=True,
    ),
    GuardrailRule(
        name="no_pii_disclosure",
        rule_type=RuleType.PII_LEAK,
        description="Response must not disclose PII (emails, phones, cards)",
        is_critical=True,
    ),
    GuardrailRule(
        name="no_fake_discounts",
        rule_type=RuleType.HALLUCINATION,
        description="Response must not promise unauthorized discounts",
        is_critical=True,
    ),
    GuardrailRule(
        name="no_unauthorized_promises",
        rule_type=RuleType.BUSINESS_POLICY,
        description="Response must not make promises outside company policy",
        is_critical=True,
    ),
    GuardrailRule(
        name="no_profanity",
        rule_type=RuleType.PROFANITY,
        description="Response must not contain profanity",
        is_critical=True,
    ),
]