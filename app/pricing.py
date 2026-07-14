"""CortexCloud model pricing — competitive rates (provider cost + ~5% margin).

Real model IDs from /x402/v1/models. Prices are invented but competitive
with market rates (chat-input / chat-output per 1M tokens, USD).
Image/video/voice are per-unit.
"""
# chat models: (id, provider, in_per_1m, out_per_1m)
CHAT_MODELS = [
    ("gemini/gemini-2.5-pro",        "google",    1.50, 10.00),
    ("gemini/gemini-2.5-flash",      "google",    0.30,  2.50),
    ("gemini/gemini-2.0-flash",      "google",    0.10,  0.40),
    ("gemini/gemini-1.5-pro",        "google",    1.25,  5.00),
    ("gemini/gemini-1.5-flash",      "google",    0.075, 0.30),
    ("groq/llama-3.3-70b-versatile", "groq",      0.59,  0.79),
    ("groq/llama-3.1-70b-versatile", "groq",      0.59,  0.79),
    ("groq/llama-3.2-90b-vision-preview","groq",  0.90,  0.90),
    ("groq/llama-3.1-8b-instant",    "groq",      0.05,  0.08),
    ("nvidia/nemotron-70b-instruct", "nvidia",    0.30,  0.45),
    ("nvidia/llama-3.3-70b-instruct","nvidia",    0.25,  0.35),
    ("nvidia/llama-3.1-8b-instruct", "nvidia",    0.04,  0.06),
    ("nvidia/mixtral-8x7b-instruct", "nvidia",    0.15,  0.20),
    ("openrouter/anthropic/claude-3.5-sonnet","anthropic",3.00,15.00),
    ("openrouter/deepseek/deepseek-chat","deepseek",0.27,1.10),
    ("openrouter/meta-llama/llama-3.1-405b-instruct","meta",2.70,2.70),
    ("openrouter/google/gemini-2.0-flash-001","google",0.10,0.40),
    ("openai/gpt-4o","openai",2.50,10.00),
    ("openai/gpt-4o-mini","openai",0.15,0.60),
]

# embeddings
EMBED_MODELS = [
    ("gemini/text-embedding-004", "google", 0.025),  # per 1M tokens
]

# modality add-ons (per unit)
MODALITY = [
    ("IMAGE", "ChatGPT Images 2.0",   "$0.06 / image"),
    ("IMAGE", "Nano Banana Pro",      "$0.10 / image"),
    ("EDIT",  "Image Editing (img2img)","from $0.05 / edit"),
    ("VIDEO", "Sora 2",               "~$0.42 / 4s 720p+audio"),
    ("VIDEO", "Seedance 2.0 Fast",    "~$1.28 / 5s 720p+audio"),
    ("MUSIC", "MiniMax Music 2.5+",   "$0.15 / track"),
    ("VOICE", "ElevenLabs Flash v2.5","$0.05 / 1k chars"),
]

# CortexCloud on-chain / data tools (real, x402-gated) — each with a price
DATA_TOOLS = [
    ("BASE BALANCE",  "On-chain balance", "Real-time ETH/native balance on Base. No API key.", "$0.002 / call"),
    ("TOKEN BALANCE", "ERC-20 holdings",  "All token holdings for any Base address. $0.003 / call", "$0.003 / call"),
    ("PRICES",        "Token prices",     "Live USDC-denominated prices across Base. $0.001 / call", "$0.001 / call"),
    ("DEX SEARCH",    "DEX pair search",  "Trending pairs, liquidity, volume on Base DEXs. $0.004 / call", "$0.004 / call"),
    ("WEB SEARCH",    "Grounded search",  "Neural web search + extraction for agents. coming soon", "soon"),
    ("IMAGE GEN",     "Text-to-image",    "Generate images, settled per call in USDC. coming soon", "soon"),
]


def pricing_payload():
    return {
        "chat": [
            {"id": i, "provider": p, "in": inn, "out": out}
            for (i, p, inn, out) in CHAT_MODELS
        ],
        "embed": [
            {"id": i, "provider": p, "price": pr}
            for (i, p, pr) in EMBED_MODELS
        ],
        "modality": [
            {"tag": t, "name": n, "price": pr} for (t, n, pr) in MODALITY
        ],
        "data": [
            {"tag": t, "name": n, "desc": d, "price": pr}
            for (t, n, d, pr) in DATA_TOOLS
        ],
        "margin_note": "Provider cost + 5% margin at settlement. No subscriptions, no minimum spend.",
    }
