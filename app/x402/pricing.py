"""
Centralized pricing configuration for x402 payment-gated routes.

Prices are in USD strings (per x402 convention: "$0.005" = half a cent).
The x402 SDK converts these to USDC atomic units automatically.
"""

# Default per-request prices by route
ROUTE_PRICING: dict[str, str] = {
    "POST /x402/v1/chat/completions": "$0.005",  # 0.5¢ per completion call
    "POST /x402/v1/responses": "$0.005",          # alias for completions
    "POST /x402/v1/embeddings": "$0.001",         # 0.1¢ per embedding call
    "GET /x402/v1/models": "$0.00",               # free — model discovery
}

# Model-specific price overrides (keyed by model name prefix).
# When a specific model is requested, the gateway could apply these
# overrides. For now, the x402 middleware charges the flat route price
# above — model-tier pricing would require the "upto" scheme.
MODEL_PRICE_TIERS: dict[str, str] = {
    "gemini": "$0.002",
    "groq": "$0.002",
    "openrouter": "$0.008",
    "gpt-4": "$0.01",
    "claude": "$0.01",
}

# Route descriptions for Bazaar discovery metadata
ROUTE_DESCRIPTIONS: dict[str, str] = {
    "POST /x402/v1/chat/completions": "OpenAI-compatible chat completions via CortexCloud AI gateway. Supports Gemini, Groq, OpenRouter, and more.",
    "POST /x402/v1/responses": "OpenAI-compatible chat completions (alias) via CortexCloud AI gateway.",
    "POST /x402/v1/embeddings": "OpenAI-compatible text embeddings via CortexCloud AI gateway.",
    "GET /x402/v1/models": "List available AI models on the CortexCloud gateway.",
}
