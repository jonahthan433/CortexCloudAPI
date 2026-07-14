"""CortexCloud public pricing endpoint (no auth, x402-discovery friendly)."""
from fastapi import APIRouter
from app.pricing import pricing_payload

router = APIRouter()


@router.get("/pricing")
async def pricing():
    return pricing_payload()
