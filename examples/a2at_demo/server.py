"""A2A-T server-side Agent demo: FastAPI + A2ATServer.

Demonstrates (see docs/zh/集成指南.md):
- Publishing an AgentCard (/.well-known/agent-card.json)
- API authentication (X-API-Key)
- Receiving A2A JSON-RPC tasks and extracting the A2A-T task prompt
- Task prompt compliance validation (check_task_prompt)
- Information negotiation across multiple rounds

Run from the project root:
    uv run uvicorn examples.a2at_demo.server:app --host 127.0.0.1 --port 8010

Prerequisites: a working LLM configured in package_data/.env.
The port can be overridden via the A2AT_DEMO_PORT environment variable;
AgentCard.url and the client address must stay in sync.
"""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI, Header, HTTPException, Response

from a2a_t.negotiation.common.enums import NegotiationStatus, NegotiationType
from a2a_t.negotiation.common.models import (
    ContinueNegotiationInput,
    NegotiationContext,
    StartNegotiationInput,
)
from a2a_t.server.a2at_server import A2ATServer

AGENT_PORT = int(os.environ.get("A2AT_DEMO_PORT", "8010"))

# A2A-T extension keys (kept in sync with a2a_t.negotiation.common.constants).
TASK_PROMPT_EXT = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"
NEGO_TEXT_EXT = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/NEGOTIATION-T"
NEGO_CTX_EXT = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/DATA-NEGOTIATION-T/v1"

# Extensions this server participates in, announced in the A2A-Extensions response header.
A2A_EXTENSIONS = ",".join([TASK_PROMPT_EXT, NEGO_TEXT_EXT, NEGO_CTX_EXT])

# Demo API key; production should issue keys from a secret manager, not hardcode them.
API_KEYS = {"demo-api-key"}

app = FastAPI(title="A2A-T Demo Server")

# Loads package_data/.env (when env_path is omitted, the SDK uses the package default path).
server = A2ATServer()


def require_auth(x_api_key: str | None = Header(default=None)) -> None:
    """Simple API-key authentication; returns 401 when the key is invalid."""
    if x_api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="unauthorized")


def _business_ready(processed_prompt: str) -> bool:
    """Business completeness check (demo placeholder; replace with real rules).

    Parses the "## 订阅条件" section; if any non-empty content line exists
    under the heading, the subscription conditions are considered provided.
    Compared to matching fixed keywords, this is robust to both Chinese and
    English subscription-condition values rendered by the LLM.
    """
    return bool(_extract_section(processed_prompt, "订阅条件").strip())


def _extract_section(prompt: str, heading: str) -> str:
    """Extract the text below the ``## <heading>`` heading (heading excluded) up to the next ``## `` heading."""
    marker = f"## {heading}"
    start = prompt.find(marker)
    if start == -1:
        return ""
    body_start = start + len(marker)
    next_marker = prompt.find("\n## ", body_start)
    if next_marker == -1:
        return prompt[body_start:]
    return prompt[body_start:next_marker]


@app.get("/.well-known/agent-card.json")
def get_agent_card() -> dict:
    """Publish the AgentCard so clients can discover capabilities, endpoints, and auth requirements."""
    return {
        "name": "a2at-demo-server",
        "description": "A2A-T demo server: incident subscription agent.",
        "url": f"http://127.0.0.1:{AGENT_PORT}/",
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "skills": [
            {
                "id": "a2at.subscribe_incident",
                "name": "Subscribe Incident",
                "description": "A2A-T structured-prompt based incident subscription.",
                "tags": ["telecom", "a2a-t"],
            }
        ],
        "securitySchemes": {"apiKey": {"type": "ApiKey", "in": "header", "name": "X-API-Key"}},
        "security": [{"apiKey": []}],
    }


@app.post("/")
def handle_message(
    payload: dict,
    response: Response,
    _: None = Depends(require_auth),
    a2a_version: str | None = Header(default=None),
) -> dict:
    """A2A JSON-RPC task entry point."""
    # Announce the A2A-T extensions this server participates in.
    response.headers["A2A-Extensions"] = A2A_EXTENSIONS

    if a2a_version != "1.0":
        raise HTTPException(status_code=400, detail="unsupported A2A version")

    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("message"), dict):
        raise HTTPException(status_code=400, detail="invalid JSON-RPC message payload")
    message = params["message"]
    extensions = message.get("extensions", {})
    processed_prompt = extensions.get(TASK_PROMPT_EXT)
    if not processed_prompt:
        raise HTTPException(status_code=400, detail="missing A2A-T task prompt")

    # Carry the negotiation context to indicate an ongoing negotiation and advance its round.
    nego_ctx = extensions.get(NEGO_CTX_EXT)
    if nego_ctx:
        return _handle_negotiation_round(processed_prompt, nego_ctx)

    # First request: validate the A2A-T task prompt for compliance.
    check_result = server.check_task_prompt(processed_prompt_text=processed_prompt)
    if check_result["success"] and _business_ready(processed_prompt):
        return {"result": _run_business(processed_prompt)}

    # Validation failed or business information is insufficient -> start information negotiation.
    return server.start_negotiation(
        StartNegotiationInput(
            type=NegotiationType.INFORMATION,
            content_text="请补充“订阅条件”（故障优先级、故障名称），例如：故障优先级为严重、高，故障名称为光纤中断。",
            facts={"failure": check_result.get("failure")},
        )
    )


def _handle_negotiation_round(processed_prompt: str, nego_ctx: dict) -> dict:
    """Advance one round of information negotiation."""
    receive = server.receive_negotiation(message=processed_prompt, context=nego_ctx)
    ctx = NegotiationContext.from_context(receive["context"])

    if _business_ready(processed_prompt):
        # Information has been completed -> agree and return the final task prompt.
        return server.continue_negotiation(
            ContinueNegotiationInput(
                context=ctx,
                status=NegotiationStatus.AGREED,
                content_text=processed_prompt,
            )
        )

    # Information is still missing -> keep negotiating.
    content = receive["message"] or "请继续补充订阅条件。"
    return server.continue_negotiation(
        ContinueNegotiationInput(
            context=ctx,
            status=NegotiationStatus.IN_PROGRESS,
            content_text=content,
        )
    )


def _run_business(processed_prompt: str) -> dict:
    """Business execution (demo placeholder)."""
    return {
        "status": "subscribed",
        "summary": f"已按 A2A-T 任务提示词完成订阅，提示词长度 {len(processed_prompt)}。",
    }