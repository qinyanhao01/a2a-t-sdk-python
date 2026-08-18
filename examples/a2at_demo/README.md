# A2A-T Client ↔ Server Demo

A complete example corresponding to `docs/zh/集成指南.md`, demonstrating a client agent connecting to a server agent over HTTP JSON-RPC. It covers:

- Fetching the server **AgentCard** (`/.well-known/agent-card.json`)
- **API authentication** (`X-API-Key`)
- Sending a task with the **A2A-T task prompt** (`Task-T/v1` extension field)
- Server-side prompt **compliance validation** (`check_task_prompt`)
- **Information negotiation** across multiple round trips (`information`)

## Directory Layout

```text
examples/a2at_demo/
├── server.py   # Server agent: FastAPI + A2ATServer
├── client.py   # Client agent: A2ATClient + HTTP send
└── README.md
```

## Prerequisites

1. Configure a working LLM in `package_data/.env` at the project root (`A2AT_LLM_*`, etc.). See `package_data/env.example`.
   > The SDK calls the LLM during initialization; without a configured LLM the server and client cannot start.
2. Install dependencies:

   ```bash
   uv sync --dev
   uv add fastapi uvicorn httpx
   ```

## Running

**Terminal 1 — start the server** (from the project root):

```bash
uv run uvicorn examples.a2at_demo.server:app --host 127.0.0.1 --port 8010
```

The default port is `8010` (overridable via the `A2AT_DEMO_PORT` environment variable; if changed, sync the client's `A2AT_DEMO_SERVER_URL`).

**Terminal 2 — run the client**:

```bash
# Complete information, succeeds in one shot
uv run python examples/a2at_demo/client.py

# Demonstrates multi-round information negotiation
# (the first round omits the specific "subscription conditions"; negotiation is triggered and completes after supplying them)
uv run python examples/a2at_demo/client.py --negotiate
```

## Negotiation Path Notes

The `subscribe_incident` slot JSON Schema has an empty `required`, so `check_task_prompt` passes for any input — negotiation is driven by the **business layer** (`_business_ready` in `server.py`): it parses the "## 订阅条件" section and, when the heading has no substantive content beneath it, the server deems the information insufficient and starts an `information` negotiation; after the client supplies the missing content and resends, the server advances to `agreed` and returns the final task prompt.

> Note: the prompt is rendered by the LLM, so the section content depends on the model output. If round 1 triggers negotiation but round 2 does not reach agreement, check the `_business_ready` conditions or adjust the input fields.

## Cleanup

```bash
uv remove fastapi uvicorn httpx   # only when this demo is no longer needed
```