import json
import time
import pytest
from httpx import ASGITransport, AsyncClient, Response
from unittest.mock import patch, AsyncMock
from sqlalchemy import select, update

from app.main import app
from app.models.billing import BillingAccount, BillingTransaction
from app.models.usage import UsageLog

# Capture the original AsyncClient methods before patching
original_post = AsyncClient.post
original_send = AsyncClient.send


@pytest.mark.asyncio
async def test_unauthorized_access():
    """Verify that accessing endpoints without key returns 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/models")
    assert response.status_code == 401
    assert "Missing Authorization Header" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_models(seed_data):
    """Verify registry lists active seeded models."""
    headers = {"Authorization": f"Bearer {seed_data['plain_key']}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/models", headers=headers)
        
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    model_ids = [m["id"] for m in data["data"]]
    assert "gpt-4o" in model_ids
    assert "claude-3-5-sonnet" in model_ids


@pytest.mark.asyncio
async def test_openai_chat_completions(seed_data, db):
    """Test successful OpenAI completions route, token counting, and account debit."""
    headers = {
        "Authorization": f"Bearer {seed_data['plain_key']}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "Tell me a joke."}
        ],
        "temperature": 0.7
    }

    openai_mock_response = {
        "id": "chatcmpl-mock-123",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "This is a mock joke from OpenAI."},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 15, "completion_tokens": 20, "total_tokens": 35}
    }

    # Fetch initial balance
    res_bal = await db.execute(
        select(BillingAccount).filter(BillingAccount.organization_id == seed_data["org_id"])
    )
    initial_balance = res_bal.scalar_one().balance

    # Pass-through mock: mock only if url targets openai, else execute original post
    async def mock_post(self_client, url, *args, **kwargs):
        if "api.openai.com" in str(url):
            return Response(200, json=openai_mock_response)
        return await original_post(self_client, url, *args, **kwargs)

    with patch("httpx.AsyncClient.post", mock_post):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/v1/chat/completions", headers=headers, json=payload)

    assert response.status_code == 200
    res_data = response.json()
    assert res_data["object"] == "chat.completion"
    assert res_data["choices"][0]["message"]["content"] == "This is a mock joke from OpenAI."
    assert res_data["usage"]["total_tokens"] == 35

    # Refresh DB session to assert billing charge
    await db.commit()
    res_bal_after = await db.execute(
        select(BillingAccount).filter(BillingAccount.organization_id == seed_data["org_id"])
    )
    final_balance = res_bal_after.scalar_one().balance

    # Cost calculations:
    # prompt: 15 * 5.00 / 1,000,000 = 0.000075
    # completion: 20 * 15.00 / 1,000,000 = 0.000300
    # total cost: 0.000375
    expected_charge = 0.000375
    assert float(final_balance) == float(initial_balance) - expected_charge

    # Assert usage log and transaction generated
    res_log = await db.execute(
        select(UsageLog).filter(UsageLog.organization_id == seed_data["org_id"])
    )
    log = res_log.scalar_one()
    assert log.model == "gpt-4o"
    assert log.prompt_tokens == 15
    assert log.completion_tokens == 20
    assert float(log.cost) == expected_charge


@pytest.mark.asyncio
async def test_anthropic_normalization(seed_data):
    """Verify Anthropic payloads map correctly and respond in OpenAI schemas."""
    headers = {"Authorization": f"Bearer {seed_data['plain_key']}"}
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "system", "content": "You are a pirate."},
            {"role": "user", "content": "Ahoy!"}
        ]
    }

    anthropic_mock_response = {
        "id": "msg_mock_anthropic_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Arrr, matey! Shiver me timbers."}],
        "model": "claude-3-5-sonnet-20240620",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 12}
    }

    async def mock_post(self_client, url, *args, **kwargs):
        if "api.anthropic.com" in str(url):
            return Response(200, json=anthropic_mock_response)
        return await original_post(self_client, url, *args, **kwargs)

    with patch("httpx.AsyncClient.post", mock_post):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/v1/chat/completions", headers=headers, json=payload)

    assert response.status_code == 200
    res_data = response.json()
    assert res_data["object"] == "chat.completion"
    assert res_data["choices"][0]["message"]["content"] == "Arrr, matey! Shiver me timbers."
    assert res_data["usage"]["prompt_tokens"] == 10
    assert res_data["usage"]["completion_tokens"] == 12


@pytest.mark.asyncio
async def test_routing_fallback_mechanism(seed_data, db):
    """Test that if gpt-4o fails (e.g. 503), it falls back automatically to claude-3-5-sonnet."""
    headers = {"Authorization": f"Bearer {seed_data['plain_key']}"}
    payload = {
        "model": "gpt-4o",  # Has capabilities["fallback_model"] = "claude-3-5-sonnet"
        "messages": [{"role": "user", "content": "Help me."}]
    }

    anthropic_mock_response = {
        "id": "msg_mock_fallback_456",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "This is a response from the fallback model."}],
        "model": "claude-3-5-sonnet-20240620",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 8, "output_tokens": 10}
    }

    async def mock_post_side_effect(self_client, url, *args, **kwargs):
        url_str = str(url)
        if "api.openai.com" in url_str:
            return Response(503, content="OpenAI Service Unavailable")
        elif "api.anthropic.com" in url_str:
            return Response(200, json=anthropic_mock_response)
        return await original_post(self_client, url, *args, **kwargs)

    with patch("httpx.AsyncClient.post", mock_post_side_effect):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/v1/chat/completions", headers=headers, json=payload)

    assert response.status_code == 200
    res_data = response.json()
    assert res_data["choices"][0]["message"]["content"] == "This is a response from the fallback model."
    
    # Assert usage log registered that we ended up routing to claude-3-5-sonnet/anthropic
    await db.commit()
    res_log = await db.execute(
        select(UsageLog).filter(UsageLog.organization_id == seed_data["org_id"]).order_by(UsageLog.created_at.desc())
    )
    logs = res_log.scalars().all()
    latest_log = logs[0]
    assert latest_log.model == "claude-3-5-sonnet"
    assert latest_log.provider == "anthropic"


@pytest.mark.asyncio
async def test_embeddings_completions(seed_data):
    """Verify embeddings route sends payload and records usage."""
    headers = {"Authorization": f"Bearer {seed_data['plain_key']}"}
    payload = {
        "model": "text-embedding-3-small",
        "input": "Hello world"
    }

    openai_mock_response = {
        "object": "list",
        "data": [{
            "object": "embedding",
            "index": 0,
            "embedding": [0.0123, -0.456, 0.789]
        }],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 2, "total_tokens": 2}
    }

    async def mock_post(self_client, url, *args, **kwargs):
        if "api.openai.com" in str(url):
            return Response(200, json=openai_mock_response)
        return await original_post(self_client, url, *args, **kwargs)

    with patch("httpx.AsyncClient.post", mock_post):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/v1/embeddings", headers=headers, json=payload)

    assert response.status_code == 200
    res_data = response.json()
    assert res_data["object"] == "list"
    assert res_data["data"][0]["embedding"] == [0.0123, -0.456, 0.789]
    assert res_data["usage"]["prompt_tokens"] == 2


@pytest.mark.asyncio
async def test_billing_exhaustion_block(seed_data, db):
    """Verify that a gateway call fails with 402 if organization balance is exhausted."""
    # 1. Update balance to 0.00
    await db.execute(
        update(BillingAccount)
        .filter(BillingAccount.organization_id == seed_data["org_id"])
        .values(balance=0.000000)
    )
    await db.commit()

    headers = {"Authorization": f"Bearer {seed_data['plain_key']}"}
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello"}]
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/v1/chat/completions", headers=headers, json=payload)

    assert response.status_code == 402
    assert "Billing balance is exhausted" in response.json()["detail"]


@pytest.mark.asyncio
async def test_simulated_deposit_decimal(seed_data, db):
    """Verify that dashboard Simulated Deposit functions without float TypeErrors."""
    # 1. Login user to get JWT token
    login_payload = {
        "username": "developer@example.com",
        "password": "devpassword"
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login_res = await ac.post("/v1/dashboard/auth/login", data=login_payload)
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]

        # 2. Call deposit
        headers = {"Authorization": f"Bearer {token}"}
        deposit_res = await ac.post("/v1/dashboard/billing/deposit?amount=25.50", headers=headers)
        assert deposit_res.status_code == 200
        assert deposit_res.json()["new_balance"] > 25.0


@pytest.mark.asyncio
async def test_admin_provider_health_dynamic(seed_data, db):
    """Verify admin dynamic health monitor endpoint functions."""
    # 1. Login user (who is seeded as admin in tests/conftest.py!)
    login_payload = {
        "username": "admin@cortexcloud.ai",
        "password": "adminpassword"
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login_res = await ac.post("/v1/dashboard/auth/login", data=login_payload)
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]

        headers = {"Authorization": f"Bearer {token}"}
        health_res = await ac.get("/v1/admin/provider-health", headers=headers)
        assert health_res.status_code == 200
        data = health_res.json()
        assert "openai" in data
        assert "status" in data["openai"]
