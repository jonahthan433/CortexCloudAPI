"""
x402 payment-gated ON-CHAIN Base data endpoints (Phase B extension).

Backed by PUBLIC Base JSON-RPC endpoints (no API key required). Provides
agent-friendly reads: native ETH balance, ERC-20 token balance, and account
nonce. Falls back across several public RPCs for resilience.
"""
import logging
import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("cortexcloud.x402.onchain")

router = APIRouter()

# Public Base RPC endpoints (no key). Tried in order; first success wins.
RPC_ENDPOINTS = [
    "https://mainnet.base.org",
    "https://base-rpc.publicnode.com",
    "https://1rpc.io/base",
]

_HEADERS = {"Content-Type": "application/json", "User-Agent": "CortexCloud/1.0"}

# Common ERC-20 token addresses on Base (for convenience defaults)
KNOWN_TOKENS = {
    "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "weth": "0x4200000000000000000000000000000000000006",
    "dai": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    "usdt": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
}

ERC20_BALANCE_SELECTOR = "0x70a08231"  # balanceOf(address)


def _addr(a: str) -> str:
    return a.lower().replace("0x", "").rjust(64, "0")


def _decode_hex_int(hexstr: str) -> int:
    return int(hexstr, 16)


async def _rpc_call(method: str, params: list) -> dict | None:
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    last_err = None
    async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS) as c:
        for url in RPC_ENDPOINTS:
            try:
                r = await c.post(url, json=payload)
                if r.status_code == 200:
                    j = r.json()
                    if "result" in j:
                        return j
            except Exception as e:
                last_err = e
                continue
    if last_err:
        logger.warning(f"onchain rpc all endpoints failed: {last_err}")
    return None


@router.get("/data/base/balance")
async def base_balance(address: str = Query(..., description="Base wallet address, e.g. 0x1234...abcd")):
    """Native ETH balance of a Base address (in wei + human-readable)."""
    res = await _rpc_call("eth_getBalance", [address, "latest"])
    if res is None or "result" not in res:
        return JSONResponse(status_code=502, content={"error": "upstream_base_rpc"})
    wei = _decode_hex_int(res["result"])
    return JSONResponse({
        "address": address,
        "network": "base",
        "chain_id": "eip155:8453",
        "wei": str(wei),
        "eth": wei / 1e18,
    })


@router.get("/data/base/token-balance")
async def base_token_balance(
    address: str = Query(..., description="Base wallet address"),
    token: str = Query(..., description="Token address OR symbol (usdc, weth, dai, usdt)"),
):
    """ERC-20 token balance for a Base address. token=usdc uses the canonical USDC."""
    token_addr = KNOWN_TOKENS.get(token.lower(), token)
    data = ERC20_BALANCE_SELECTOR + _addr(address)
    res = await _rpc_call("eth_call", [{"to": token_addr, "data": data}, "latest"])
    if res is None or "result" not in res:
        return JSONResponse(status_code=502, content={"error": "upstream_base_rpc"})
    raw = res["result"]
    if isinstance(raw, str) and raw.startswith("0x"):
        bal = _decode_hex_int(raw)
    else:
        bal = 0
    return JSONResponse({
        "address": address,
        "token": token_addr,
        "network": "base",
        "raw": str(bal),
        "note": "balance is in token base units (USDC has 6 decimals)",
    })


@router.get("/data/base/nonce")
async def base_nonce(address: str = Query(..., description="Base wallet address")):
    """Transaction count (nonce) for a Base address."""
    res = await _rpc_call("eth_getTransactionCount", [address, "latest"])
    if res is None or "result" not in res:
        return JSONResponse(status_code=502, content={"error": "upstream_base_rpc"})
    return JSONResponse({
        "address": address,
        "network": "base",
        "nonce": _decode_hex_int(res["result"]),
    })
