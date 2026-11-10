"""Deterministic demo: scripted transport, frozen clock, byte-identical output."""
from __future__ import annotations

import json
import tempfile

from .engine import WorkflowEngine
from .state import Journal
from .twilio import TwilioClient
from .workflow import parse_workflow

WORKFLOW = {
    "name": "renewal-reminder",
    "steps": [
        {"type": "send", "to": "+15552220001",
         "body": "Hi {name}, your renewal is due on {date}."},
        {"type": "wait", "seconds": 60},
        {"type": "send", "to": "+15552220001",
         "body": "Reminder {name}: reply DONE to confirm. Ref: {step_0_sid}"},
    ],
}

WORKFLOW_JSON = json.dumps(WORKFLOW, indent=2)


class _ScriptedTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, method: str, url: str, headers: dict[str, str],
                 body: bytes | None, timeout: float) -> tuple[int, dict[str, str], bytes]:
        self.calls += 1
        payload = json.dumps({"sid": f"SM{self.calls:024d}", "status": "queued"}).encode()
        return 201, {}, payload


class _FrozenClock:
    def __init__(self) -> None:
        self.now = 1_760_000_000.0

    def __call__(self) -> float:
        return self.now


def run_demo() -> str:
    lines: list[str] = ["twilio-workflow-engine demo", "=" * 26, ""]
    workflow = parse_workflow(WORKFLOW_JSON)
    lines.append(f"workflow: {workflow.summary()}")
    lines.append("")
    transport = _ScriptedTransport()
    clock = _FrozenClock()
    sleeps: list[float] = []
    client = TwilioClient("ACdemo", "demo-token", transport=transport, sleep=sleeps.append)
    with tempfile.TemporaryDirectory() as tmp:
        engine = WorkflowEngine(client, Journal(f"{tmp}/demo-state.jsonl"),
                                clock=clock, sleep=sleeps.append)
        run = engine.start(workflow, to_number="+15550001111",
                           context={"name": "Ada", "date": "Nov 30"}, run_id="demo-run-01")
    lines.append(f"run: {run.run_id}")
    for index in range(len(run.steps)):
        result = run.step_results.get(index)
        if result is None:
            lines.append(f"  step {index}: SKIPPED (guard false)")
        else:
            detail = result["body"] if result["kind"] == "send" else f'waited {result["seconds"]}s'
            lines.append(f"  step {index}: {result['kind']} -> {detail}")
    lines.append("")
    lines.append(f"status: {run.status}, sends: {transport.calls}, sleeps: {sleeps}")
    lines.append(f"final context: {json.dumps(run.context, sort_keys=True)}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(run_demo())
