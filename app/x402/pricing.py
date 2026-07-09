"""
Centralized pricing configuration for x402 payment-gated routes.

Prices are in USD strings (e.g. "$0.005" = 0.5¢ per request).
These are converted to USDC atomic units (6 decimals on Base) for the x402 challenge.
"""

# Default per-request prices by route (only for the /x402/v1 prefix)
ROUTE_PRICING: dict[str, str] = {
    # Chat completions
    "POST /x402/v1/chat/completions": "$0.005",
    "POST /x402/v1/responses": "$0.005",
    
    # Embeddings
    "POST /x402/v1/embeddings": "$0.001",
    
    # Models (Free)
    "GET /x402/v1/models": "$0.00",
}

# Route descriptions for x402 challenge
ROUTE_DESCRIPTIONS: dict[str, str] = {
    "POST /x402/v1/chat/completions": "OpenAI-compatible chat completions via CortexCloud AI gateway.",
    "POST /x402/v1/responses": "OpenAI-compatible chat completions (alias) via CortexCloud AI gateway.",
    "POST /x402/v1/embeddings": "OpenAI-compatible text embeddings via CortexCloud AI gateway.",
}


def usd_to_usdc_atomic(price_str: str) -> str:
    """
    Converts a USD price string to USDC atomic units (string representing integer).
    USDC on Base has 6 decimals.
    Example: "$0.005" -> "5000"
             "$0.001" -> "1000"
    """
    val = price_str.lstrip('$')
    try:
        amount = float(val)
        atomic = int(amount * 1_000_000)
        return str(atomic)
    except ValueError:
        return "0"
