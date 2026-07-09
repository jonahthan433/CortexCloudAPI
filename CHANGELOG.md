# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-09

### Added
- **OpenAI Compatible Gateway Endpoints**: Complete support for `/v1/chat/completions` (both streaming and non-streaming) and `/v1/embeddings`.
- **Multi-Provider Integrations**: Native client wrappers for OpenAI, Anthropic (Claude), Gemini, and Groq.
- **Failover Routing & Fallback dispatcher**: Automatic redirection to pre-configured fallback models with exponential backoff on retryable status codes.
- **Ledger Billing & Top-Up Systems**: Balance-checking gatekeepers blocking exhausted organizations with `402 Payment Required` errors and transaction ledger tables.
- **Rate Limiter Engine**: High-concurrency safe Redis-backed sliding window rate limiters (minute, daily, and monthly limits) with database fallback checks.
- **Structured Database & Migrations**: Version-controlled Alembic migrations with column-level index profiling on all foreign keys.
- **Dynamic Provider Health Monitor**: Aggregate admin API calculating upstream error rates within a rolling 15-minute window.
- **Developer Token caching**: Startup lifespan context warming and caching tiktoken tokenizers in memory.
