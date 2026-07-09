# x402 Payment Integration — Testing Guide

This guide explains how to test the x402 payment-gated endpoints on CortexCloud API.

## Overview

CortexCloud API supports two authentication modes:

| Path Prefix | Auth Method | Description |
|---|---|---|
| `/v1/*` | API Key (`Authorization: Bearer sk-...`) | Traditional API key authentication |
| `/x402/v1/*` | x402 Payment (USDC on Base) | Pay-per-request via crypto |

Both route sets use the same underlying AI models and gateway infrastructure.

## Prerequisites

- CortexCloud API running (locally or deployed)
- A crypto wallet (MetaMask, Coinbase Wallet, or any EVM wallet)
- Testnet USDC on Base Sepolia (for testing)

## 1. Configure Environment

Add these to your `.env` file:

```bash
X402_ENABLED=true
WALLET_ADDRESS=0xYourBaseWalletAddress   # Your wallet to receive payments
X402_FACILITATOR_URL=https://x402.org/facilitator  # Testnet facilitator
X402_NETWORK=eip155:84532                # Base Sepolia testnet
```

## 2. Get Testnet USDC

For Base Sepolia testnet:

1. Get Base Sepolia ETH from the [Base Sepolia Faucet](https://www.coinbase.com/faucets/base-ethereum-goerli-faucet)
2. Get testnet USDC from the [Circle Testnet Faucet](https://faucet.circle.com/) (select Base Sepolia)

## 3. Verify x402 is Active

```bash
# Check discovery endpoint
curl http://localhost:8000/.well-known/x402.json | python3 -m json.tool
```

Expected response:
```json
{
    "x402": true,
    "version": 2,
    "facilitator": "https://x402.org/facilitator",
    "network": "eip155:84532",
    "wallet": "0xYour...",
    "endpoints": {
        "chat_completions": "/x402/v1/chat/completions",
        "embeddings": "/x402/v1/embeddings",
        "models": "/x402/v1/models"
    }
}
```

## 4. Test 402 Challenge

Making a request without payment should return `402 Payment Required`:

```bash
curl -v http://localhost:8000/x402/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected:
- HTTP status: `402 Payment Required`
- `PAYMENT-REQUIRED` header with base64-encoded payment requirements
- Decoded header contains: `accepts` array with price, network, payTo, scheme

## 5. Test with x402 Python Client

Install the x402 client SDK:

```bash
pip install x402
```

Test script:

```python
import asyncio
from eth_account import Account
from x402 import x402Client
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

async def main():
    # Use a test private key (NEVER use a real key with funds!)
    account = Account.from_key("0xYourTestPrivateKey")
    
    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))
    
    async with x402HttpxClient(client) as http:
        response = await http.post(
            "http://localhost:8000/x402/v1/chat/completions",
            json={
                "model": "gemini-2.5-flash",
                "messages": [{"role": "user", "content": "Hello, what is 2+2?"}],
            },
        )
        await response.aread()
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")

asyncio.run(main())
```

## 6. Verify Existing Routes Are Unaffected

```bash
# Health check (no auth required)
curl http://localhost:8000/health

# API-key-authenticated routes still work
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-your-api-key"
```

## 7. Models Endpoint (Free)

The models discovery endpoint is free — no payment needed:

```bash
curl http://localhost:8000/x402/v1/models | python3 -m json.tool
```

## Switching to Mainnet

When ready for production:

1. Update `.env`:
   ```bash
   X402_NETWORK=eip155:8453  # Base mainnet
   X402_FACILITATOR_URL=https://facilitator.mogami.tech  # Or any production facilitator
   ```

2. Production facilitator options (see [docs.x402.org/dev-tools/facilitators](https://docs.x402.org/dev-tools/facilitators)):
   - **CDP Facilitator** — Coinbase-hosted with KYT/OFAC compliance
   - **Dexter** — Free, no account required
   - **Mogami** — Free, developer-focused
   - **PayAI** — Multi-network, no API keys

3. Restart the service:
   ```bash
   sudo systemctl restart cortexcloud.service
   ```

## x402scan Discovery

Your endpoints will automatically appear on [x402scan](https://x402scan.com) once:

1. The x402 payment middleware is active with Bazaar extension metadata
2. The facilitator indexes your endpoints from the first paid request
3. Allow a few minutes for indexing after the first successful payment

The Bazaar discovery extension is automatically configured in `app/x402/setup.py` with input/output schemas for all endpoints.

## Pricing

Default pricing (configured in `app/x402/pricing.py`):

| Endpoint | Price per Request |
|---|---|
| `POST /x402/v1/chat/completions` | $0.005 (0.5¢) |
| `POST /x402/v1/embeddings` | $0.001 (0.1¢) |
| `GET /x402/v1/models` | Free |

To adjust pricing, edit the `ROUTE_PRICING` dict in `app/x402/pricing.py` and restart.
