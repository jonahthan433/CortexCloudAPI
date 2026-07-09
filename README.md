# CortexCloud API Gateway

CortexCloud API is a production-ready, enterprise-grade, high-performance AI gateway designed to unify multiple AI providers (OpenAI, Anthropic, Gemini, Groq) under a single OpenAI-compatible API surface. 

It provides secure authentication, Redis-backed sliding window rate limiters, precision billing ledger accounting, automatic fallback routing, and real-time observability.

---

## Architecture Diagram

The diagram below shows the end-to-end request lifecycle through the CortexCloud gateway:

```mermaid
sequenceDiagram
    autonumber
    actor Client as Developer / Client
    participant GW as FastAPI Gateway
    participant Redis as Redis Cache
    participant DB as Postgres Database
    participant Upstream as Upstream AI Provider<br/>(OpenAI/Claude/Gemini/Groq)

    Client->>GW: POST /v1/chat/completions (Bearer Key)
    activate GW
    
    GW->>DB: Query API Key & Billing Balance (Locked FOR UPDATE)
    activate DB
    DB-->>GW: Return key details & Balance ($10.00)
    deactivate DB

    alt Balance <= 0
        GW-->>Client: 402 Payment Required (Exhausted Balance)
    else Balance > 0
        GW->>Redis: Check Rate Limits (Minute/Daily/Monthly)
        activate Redis
        Redis-->>GW: OK (Not Rate Limited)
        deactivate Redis

        GW->>DB: Resolve model specs & fallback chains
        activate DB
        DB-->>GW: Active Model specs (e.g., gpt-4o -> claude-3-5)
        deactivate DB

        GW->>Upstream: Execute completions request with retries
        activate Upstream
        alt Primary Fails (HTTP 429/5xx)
            Upstream-->>GW: Error response
            Note over GW: Log warning & trigger retry or fallback to Claude 3.5
            GW->>Upstream: Execute completion on fallback model
            Upstream-->>GW: Success Response
        else Success
            Upstream-->>GW: Success Response
        end
        deactivate Upstream

        GW->>GW: Calculate prompt/completion tokens (tiktoken) & costs
        GW->>DB: Deduct costs, Log usage transaction (UsageLog)
        activate DB
        DB-->>GW: Commited
        deactivate DB

        GW-->>Client: Return OpenAI-compatible response JSON / Stream
    end
    deactivate GW
```

---

## Features

*   **100% OpenAI Compatibility**: Drop-in replacement for OpenAI endpoints (`/v1/chat/completions` and `/v1/embeddings`).
*   **Unified AI Provider Support**: Native integration with **OpenAI**, **Anthropic**, **Gemini**, and **Groq** APIs.
*   **Automatic Failover & Retries**: Configurable fallback routes and retry policies (exponential backoff) to keep applications up during provider outages.
*   **Ledger Billing & Credit Control**: High-precision balance checks (`with_for_update` row locks) preventing credit exploitation with instant `402 Payment Required` errors.
*   **Redis-Backed Sliding Window Rate Limiting**: Limiters covering minute (RPM), daily, and monthly request volumes with graceful database fallbacks.
*   **Tokenizer Pre-Warming**: Pre-cached vocabularies in memory at server boot to avoid synchronous blocking I/O.
*   **Observability & Health Checks**: Structured JSON logs and rollups showing provider error rates in real-time.

---

## Quick Start & Installation

### Prerequisites
*   Python 3.12+
*   PostgreSQL and Redis (or Docker/Podman)

### 1. Clone & Setup Virtual Environment
```bash
git clone git@github.com:jonahthan433/CortexCloudAPI.git
cd CortexCloudAPI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your variables:
```bash
cp .env.example .env
```

### 3. Spin up Databases
To start local database instances using Docker Compose:
```bash
docker-compose up -d
```

### 4. Run Database Migrations
Apply Alembic migrations to build the tables and create schema indexes:
```bash
alembic upgrade head
```

### 5. Seed Developer Workspace
Seed default models, developer admin accounts, and trial balances:
```bash
python -m app.scratch.seed_dev
```

### 6. Start the API Gateway
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Swagger API documentation will be available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

---

## API Examples

### Chat Completions
Route chat completion requests through CortexCloud by substituting the Bearer key and API endpoint:

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer cx-live-devkey1234567890" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [
      {"role": "user", "content": "Explain quantum computing in one sentence."}
    ]
  }'
```

---

## Testing

To run the automated integration test suite:
```bash
pytest tests/test_gateway.py -p no:warnings
```

---

## Deployment

CortexCloud API can be deployed to production in a containerized environment. Build the production Docker image using:

```bash
docker build -t cortexcloud-api:latest .
```

---

## Project Roadmap

- [ ] **Redis Model Registry Caching**: Add TTL-based memory caching for active model routes to reduce database reads.
- [ ] **Strict User Password Complexity**: Integrate complexity scoring checks on registrations.
- [ ] **Custom Timeout Settings per Model**: Let developers define custom timeout capabilities inside the registry.
- [ ] **Advanced Usage Analytics Dashboard**: Build a React/Next.js frontend UI for charts, logs, and token tracking.
