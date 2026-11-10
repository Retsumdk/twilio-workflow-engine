"""Workflow definitions: parsing, validation, templates, guards."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .errors import TemplateError, WorkflowError

__all__ = ["Guard", "Step", "Workflow", "load_workflow_file", "parse_workflow", "render"]

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_TEMPLATE_VAR = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
MAX_STEPS = 100
MAX_WAIT_SECONDS = 86_400


@dataclass(frozen=True)
class Guard:
    var: str
    equals: Any


@dataclass(frozen=True)
class Step:
    type: str  # "send" | "wait"
    index: int = 0
    to: str | None = None
    body: str | None = None
    seconds: float | None = None
    when: Guard | None = None


@dataclass(frozen=True)
class Workflow:
    name: str
    steps: tuple[Step, ...]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.type] = counts.get(step.type, 0) + 1
        parts = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
        return f"{self.name} ({len(self.steps)} steps: {parts})"


def render(template: str, context: dict[str, Any]) -> str:
    def substitute(match: re.Match[str]) -> str:
        var = match.group(1)
        if var not in context:
            raise TemplateError(f"missing variable {var!r} in template {template!r}")
        return str(context[var])

    return _TEMPLATE_VAR.sub(substitute, template)


def _parse_guard(raw: Any, where: str) -> Guard | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WorkflowError(f"{where}: 'when' must be an object with 'var' and 'equals'")
    if set(raw) != {"var", "equals"}:
        raise WorkflowError(f"{where}: 'when' needs exactly 'var' and 'equals', got {sorted(raw)}")
    var = raw["var"]
    if not isinstance(var, str) or not var.isidentifier():
        raise WorkflowError(f"{where}: 'when.var' must be an identifier, got {var!r}")
    if isinstance(raw["equals"], (dict, list)):
        raise WorkflowError(f"{where}: 'when.equals' must be a scalar")
    return Guard(var=var, equals=raw["equals"])


def _parse_step(raw: Any, index: int) -> Step:
    where = f"step {index}"
    if not isinstance(raw, dict):
        raise WorkflowError(f"{where}: must be an object")
    guard = _parse_guard(raw.get("when"), where)
    keys = set(raw) - {"when"}
    step_type = raw.get("type")
    if step_type == "send":
        if keys - {"type", "to", "body"}:
            raise WorkflowError(f"{where}: unknown fields {sorted(keys - {'type', 'to', 'body'})}")
        to = raw.get("to")
        body = raw.get("body")
        if not isinstance(to, str) or not E164.match(to):
            raise WorkflowError(f"{where}: 'to' must be E.164 (e.g. +15551234567), got {to!r}")
        if not isinstance(body, str) or not body.strip():
            raise WorkflowError(f"{where}: 'body' must be a non-empty string")
        return Step(type="send", index=index, to=to, body=body, when=guard)
    if step_type == "wait":
        if keys - {"type", "seconds"}:
            raise WorkflowError(f"{where}: unknown fields {sorted(keys - {'type', 'seconds'})}")
        seconds = raw.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0 \
                or seconds > MAX_WAIT_SECONDS:
            raise WorkflowError(f"{where}: 'seconds' must be a number in [0, {MAX_WAIT_SECONDS}]")
        return Step(type="wait", index=index, seconds=seconds, when=guard)
    raise WorkflowError(f"{where}: unknown type {step_type!r} (expected 'send' or 'wait')")


def parse_workflow(source: str | dict[str, Any]) -> Workflow:
    if isinstance(source, str):
        try:
            data = json.loads(source)
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"invalid workflow JSON: {exc}") from exc
    else:
        data = source
    if not isinstance(data, dict):
        raise WorkflowError("workflow must be a JSON object")
    unknown = set(data) - {"name", "steps"}
    if unknown:
        raise WorkflowError(f"unknown workflow fields: {sorted(unknown)}")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise WorkflowError("'name' must be a non-empty string")
    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise WorkflowError("'steps' must be a non-empty array")
    if len(steps_raw) > MAX_STEPS:
        raise WorkflowError(f"too many steps: {len(steps_raw)} > {MAX_STEPS}")
    steps = tuple(_parse_step(raw, i) for i, raw in enumerate(steps_raw))
    return Workflow(name=name.strip(), steps=steps)


def load_workflow_file(path: str) -> Workflow:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise WorkflowError(f"cannot read workflow file {path}: {exc}") from exc
    return parse_workflow(text)
