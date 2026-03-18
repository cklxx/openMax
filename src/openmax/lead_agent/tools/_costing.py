"""Cost estimation for agent dispatch."""

from __future__ import annotations

from dataclasses import dataclass

# Approximate cost per 1M tokens (input + output blended) by agent type.
# Conservative estimates — actual cost depends on model selection and usage.
MODEL_PRICING: dict[str, float] = {
    "claude-code": 9.0,
    "codex": 7.5,
    "opencode": 9.0,
    "generic": 9.0,
}

_DEFAULT_PRICE_PER_M = 9.0
_CHARS_PER_TOKEN = 4
_OUTPUT_MULTIPLIER = 2


@dataclass
class CostEstimate:
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float

    @property
    def estimated_tokens(self) -> int:
        return self.estimated_input_tokens + self.estimated_output_tokens


def estimate_task_cost(prompt_len: int, agent_type: str) -> CostEstimate:
    """Estimate token usage and cost for a dispatch.

    Uses prompt character length / 4 for input tokens, 2x for output tokens,
    and a per-agent pricing table for USD estimate.
    """
    input_tokens = max(prompt_len // _CHARS_PER_TOKEN, 1)
    output_tokens = input_tokens * _OUTPUT_MULTIPLIER
    price_per_m = MODEL_PRICING.get(agent_type, _DEFAULT_PRICE_PER_M)
    total_tokens = input_tokens + output_tokens
    cost_usd = round(total_tokens * price_per_m / 1_000_000, 6)
    return CostEstimate(
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_cost_usd=cost_usd,
    )
