# AI Configuration API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Connect existing AI settings UI to persistent CRUD through a typed Axios module.
**Architecture:** API → existing service → DAO. Frontend page/context → API module → shared Axios client → Vite proxy.
**Tech Stack:** Existing FastAPI/SQLAlchemy/PyMySQL, React/TypeScript/Vite, Axios.
**Spec:** docs/superpowers/specs/2026-09-18-ai-config-api-design.md

## Tasks

- [x] Backend tests first: tests/api/test_ai_validation.py plus tests/integration/test_ai_api.py cover no input leakage, three types, CRUD, deletion, optimistic version and default switch using temporary cloud database.
- [x] Add api/v1/ai_model_configs.py, response/page and default-action schemas, service dependency and validation exception handler. Keep existing Python service tests compatible.
- [x] Add Axios and api/http.ts, api/types/ai-model-configs.ts, api/modules/ai-model-configs.ts; configure Vite proxy and document request conventions.
- [x] Replace in-memory AI session and stale unsupported form fields with paginated live requests, mutation states, retained inputs on failure, version conflict recovery, enabled status, secret retention/clear and explicit default action.
- [x] Run backend tests on real isolated MySQL, frontend typecheck/build, then browser CRUD persistence flow through Axios/Vite with a temporary backend/schema. Clean temporary test schema/processes only.
- [x] Review changed code, fix verified issues, update README/API documentation, restart owned development services and verify real list HTTP endpoint.
