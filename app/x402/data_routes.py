"""
x402 payment-gated DATA marketplace endpoints (Phase B).

Keyless upstreams (no external API keys required):
  - CoinGecko  : token prices / market data
  - DEXScreener: DEX pairs / liquidity / price discovery

These are read-only proxies. The x402 middleware already gates them by path,
so callers must pay before the proxy runs. Responses are normalized to a
simple JSON shape agents can consume.

NOTE: all dynamic values are passed as QUERY params (never path params) so the
x402 middleware path-only pricing lookup matches exactly.
"""
import logging
import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("cortexcloud.x402.data")

router = APIRouter()

CG_BASE = "https://api.coingecko.com/api/v3"
DEX_BASE = "https://api.dexscreener.com/latest/dex"

_HEADERS = {"Accept": "application/json", "User-Agent": "CortexCloud/1.0"}


@router.get("/data/prices")
async def data_prices(
    ids: str = Query(..., description="Comma-separated CoinGecko coin ids, e.g. bitcoin,ethereum"),
    vs: str = Query("usd", description="Comma-separated vs currencies, e.g. usd,eur"),
):
    """Spot prices from CoinGecko. e.g. ?ids=bitcoin,ethereum&vs=usd."""
    url = f"{CG_BASE}/simple/price?ids={ids}&vs_currencies={vs}"
    async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS) as c:
        r = await c.get(url)
        if r.status_code != 200:
            return JSONResponse(status_code=502, content={"error": "upstream_coingecko", "detail": r.text[:300]})
        return JSONResponse(r.json())


@router.get("/data/coins/search")
async def data_coin_search(q: str = Query(..., description="Coin name or symbol to search")):
    """Search CoinGecko coins by name/symbol."""
    url = f"{CG_BASE}/search?query={q}"
    async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS) as c:
        r = await c.get(url)
        if r.status_code != 200:
            return JSONResponse(status_code=502, content={"error": "upstream_coingecko", "detail": r.text[:300]})
        return JSONResponse(r.json())


@router.get("/data/dex/search")
async def data_dex_search(q: str = Query(..., description="Token symbol or address to search, e.g. WETH")):
    """Search DEX pairs on DEXScreener by token/address."""
    url = f"{DEX_BASE}/search?q={q}"
    async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS) as c:
        r = await c.get(url)
        if r.status_code != 200:
            return JSONResponse(status_code=502, content={"error": "upstream_dexscreener", "detail": r.text[:300]})
        return JSONResponse(r.json())


@router.get("/data/dex/pairs")
async def data_dex_pairs(
    chain: str = Query(..., description="Chain id, e.g. ethereum, base, solana"),
    pair: str = Query(..., description="Token address or pair address"),
):
    """Top DEX pairs for a token on a given chain. ?chain=ethereum&pair=0x..."""
    url = f"{DEX_BASE}/tokens/{chain}/{pair}"
    async with httpx.AsyncClient(timeout=12.0, headers=_HEADERS) as c:
        r = await c.get(url)
        if r.status_code != 200:
            return JSONResponse(status_code=502, content={"error": "upstream_dexscreener", "detail": r.text[:300]})
        return JSONResponse(r.json())
