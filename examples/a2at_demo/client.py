"""A2A-T client-side Agent demo: connects to the server Agent described in docs/zh/集成指南.md.

Flow: fetch AgentCard -> generate A2A-T task prompt -> send the task with
authentication -> handle negotiation -> reach agreement.

Usage (from the project root, start the server first):
    uv run python examples/a2at_demo/client.py            # complete information, succeeds in one shot
    uv run python examples/a2at_demo/client.py --negotiate  # demonstrates multi-round information negotiation

Prerequisites: a working LLM configured in package_data/.env.
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.negotiation.common.enums import NegotiationStatus
from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext

# A2A-T extension keys (kept in sync with a2a_t.negotiation.common.constants).
TASK_PROMPT_EXT = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"
NEGO_TEXT_EXT = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/NEGOTIATION-T"
NEGO_CTX_EXT = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/DATA-NEGOTIATION-T/v1"

SERVER_URL = os.environ.get("A2AT_DEMO_SERVER_URL", "http://127.0.0.1:8010")
API_KEY = "demo-api-key"
HEADERS = {
    "Content-Type": "application/json",
    "A2A-Version": "1.0",
    "A2A-Extensions": ",".join([TASK_PROMPT_EXT, NEGO_TEXT_EXT, NEGO_CTX_EXT]),
    "X-API-Key": API_KEY,
}

client = A2ATClient()


def fetch_agent_card() -> dict:
    """Fetch the server AgentCard."""
    resp = httpx.get(f"{SERVER_URL}/.well-known/agent-card.json", timeout=10)
    resp.raise_for_status()
    return resp.json()


def send_task(processed_prompt: str, nego_ctx: dict | None = None) -> dict:
    """Send a task over A2A JSON-RPC and return the server response payload."""
    extensions: dict[str, object] = {TASK_PROMPT_EXT: processed_prompt}
    if nego_ctx is not None:
        extensions[NEGO_CTX_EXT] = nego_ctx
    resp = httpx.post(
        f"{SERVER_URL}/",
        headers=HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"text": "subscribe_incident"}],
                    "extensions": extensions,
                }
            },
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def generate(extra: dict) -> str:
    """Generate an A2A-T task prompt."""
    result = client.generate_task_prompt({"scenario": "subscribe_incident", **extra})
    if not result.success:
        raise RuntimeError(f"generate_task_prompt failed: {result.failure.to_dict()}")
    return result.prompt_text


def run_simple() -> None:
    """Complete-information path that succeeds in one shot."""
    agent_card = fetch_agent_card()
    print(f"[AgentCard] Fetched server: {agent_card['name']} (skill: {agent_card['skills'][0]['id']})")

    prompt = generate(
        {
            "objective": "订阅网络设备的故障通知。",
            "subscription_condition_incident_level": ["critical"],
            "subscription_condition_incident_name": ["fiber break"],
        }
    )
    print("[1] Sending task prompt...")
    payload = send_task(prompt)
    if "result" in payload:
        print(f"[OK] Server business result: {payload['result']}")
    else:
        print(f"[!] Unexpected negotiation response: {payload}")


def run_negotiate() -> None:
    """Multi-round information negotiation path."""
    agent_card = fetch_agent_card()
    print(f"[AgentCard] Fetched server: {agent_card['name']}")

    # Round 1: no subscription conditions provided -> server business check fails -> negotiation starts.
    prompt = generate({"objective": "请订阅网络设备的故障通知。"})
    print("[1] Sending task prompt (subscription conditions missing)...")
    payload = send_task(prompt)
    if "result" in payload:
        # Succeeded in round 1 (the prompt already contained conditions); negotiation was not triggered.
        print(f"[*] Succeeded on the first attempt, no negotiation triggered: {payload['result']}")
        return

    nego_text = payload[NEGO_TEXT_EXT]
    nego_ctx = payload[NEGO_CTX_EXT]
    print(f"[negotiation] Server asks: {nego_text}")

    # Client handles the negotiation: record state, add the subscription conditions, and regenerate the full prompt.
    receive = client.receive_negotiation(message=nego_text, context=nego_ctx)
    prompt_full = generate(
        {
            "objective": "请订阅网络设备的故障通知。",
            "subscription_condition_incident_level": ["critical", "high"],
            "subscription_condition_incident_name": ["fiber break", "board fault"],
        }
    )
    continue_payload = client.continue_negotiation(
        ContinueNegotiationInput(
            context=NegotiationContext.from_context(receive["context"]),
            status=NegotiationStatus.IN_PROGRESS,
            content_text=prompt_full,
        )
    )
    print("[2] Re-sending with the subscription conditions added...")
    payload2 = send_task(prompt_full, nego_ctx=continue_payload[NEGO_CTX_EXT])

    if TASK_PROMPT_EXT in payload2:
        print("[OK] Negotiation agreed, final task prompt:")
        print(payload2[TASK_PROMPT_EXT])
    else:
        print(f"[!] Negotiation still in progress, server response: {payload2}")


def main() -> None:
    parser = argparse.ArgumentParser(description="A2A-T client Agent demo")
    parser.add_argument("--negotiate", action="store_true", help="demonstrate multi-round information negotiation")
    args = parser.parse_args()
    try:
        run_negotiate() if args.negotiate else run_simple()
    except httpx.HTTPStatusError as error:
        print(f"[error] HTTP {error.response.status_code}: {error.response.text}", file=sys.stderr)
        sys.exit(1)
    except httpx.ConnectError:
        print("[error] Cannot connect to the server; start the uvicorn service first.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()