"""Workflow engine: run declarative SMS workflows with crash-resume."""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from .errors import TweError, WorkflowError
from .state import Journal, Run
from .twilio import TwilioClient
from .workflow import Workflow, render

Clock = Callable[[], float]
Sleep = Callable[[float], None]


def _snapshot(step: Any) -> dict[str, Any]:
    """Serialize a parsed step back to its plain-dict journal form."""
    snap: dict[str, Any] = {"type": step.type}
    for key in ("to", "body", "seconds"):
        value = getattr(step, key)
        if value is not None:
            snap[key] = value
    if step.when is not None:
        snap["when"] = {"var": step.when.var, "equals": step.when.equals}
    return snap


class WorkflowEngine:
    def __init__(self, client: TwilioClient | None, journal: Journal,
                 clock: Clock = time.time, sleep: Sleep = time.sleep) -> None:
        self._client = client
        self._journal = journal
        self._clock = clock
        self._sleep = sleep

    def runs(self) -> dict[str, Run]:
        return self._journal.runs()

    def start(self, workflow: Workflow, to_number: str, context: dict[str, Any] | None = None,
              run_id: str | None = None) -> Run:
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        if run_id in self.runs():
            raise WorkflowError(f"run id {run_id!r} already exists")
        run = Run(run_id=run_id, workflow_name=workflow.name, to_number=to_number,
                  steps=[_snapshot(s) for s in workflow.steps],
                  context=dict(context or {}), created_at=self._clock())
        self._journal.append({"event": "run_started", "run_id": run_id,
                              "workflow_name": workflow.name, "to_number": to_number,
                              "steps": run.steps, "context": run.context, "ts": self._clock()})
        return self._execute(run)

    def resume(self, run_id: str) -> Run:
        run = self.runs().get(run_id)
        if run is None:
            raise WorkflowError(f"unknown run {run_id!r}")
        if run.status == "completed":
            raise WorkflowError(f"run {run_id!r} is already completed")
        return self._execute(run)

    def _execute(self, run: Run) -> Run:
        while run.current < len(run.steps):
            index = run.current
            raw = run.steps[index]
            guard = raw.get("when")
            satisfied = guard is not None and guard["var"] in run.context \
                and run.context[guard["var"]] == guard["equals"]
            if guard and not satisfied:
                self._journal.append({"event": "step_skipped", "run_id": run.run_id,
                                      "index": index, "ts": self._clock()})
                run.skipped.add(index)
                run.current = index + 1
                continue
            try:
                result = self._run_step(run, raw, index)
            except TweError as exc:
                self._journal.append({"event": "run_failed", "run_id": run.run_id,
                                      "error": str(exc), "ts": self._clock()})
                run.status = "failed"
                run.error = str(exc)
                return run
            self._journal.append({"event": "step_completed", "run_id": run.run_id, "index": index,
                                  "result": result, "ts": self._clock()})
            run.step_results[index] = result
            run.current = index + 1
            if raw["type"] == "send":
                run.context[f"step_{index}_sid"] = result["sid"]
                run.context[f"step_{index}_status"] = result["status"]
        self._journal.append({"event": "run_completed", "run_id": run.run_id, "ts": self._clock()})
        run.status = "completed"
        return run

    def _run_step(self, run: Run, raw: dict[str, Any], index: int) -> dict[str, Any]:
        if raw["type"] == "wait":
            self._sleep(float(raw["seconds"]))
            return {"kind": "wait", "seconds": raw["seconds"]}
        if self._client is None:
            raise WorkflowError("no Twilio client configured; set credentials to send messages")
        rendered = render(raw["body"], run.context)
        sent = self._client.send_sms(to=raw["to"], from_=run.to_number, body=rendered)
        return {"kind": "send", "sid": sent["sid"], "status": sent["status"], "body": rendered}
