"""Workflow parsing, validation, templates, guards."""
import pytest

from twilio_workflow import TemplateError, WorkflowError, parse_workflow, render


def test_parse_minimal():
    wf = parse_workflow({"name": "w", "steps": [{"type": "wait", "seconds": 5}]})
    assert wf.name == "w"
    assert wf.steps[0].type == "wait"
    assert wf.summary() == "w (1 steps: 1 wait)"


def test_send_step_validation():
    wf = parse_workflow({"name": "w", "steps": [
        {"type": "send", "to": "+15551234567", "body": "hi {name}"}]})
    assert wf.steps[0].to == "+15551234567"
    assert wf.steps[0].body == "hi {name}"


@pytest.mark.parametrize("bad", [
    {"name": "w"},
    {"name": "w", "steps": []},
    {"name": "", "steps": [{"type": "wait", "seconds": 1}]},
    {"name": "w", "steps": [], "extra": 1},
    {"name": "w", "steps": [{"type": "burst"}]},
    {"name": "w", "steps": [{"type": "send", "to": "5551234", "body": "x"}]},
    {"name": "w", "steps": [{"type": "send", "to": "+15551234567", "body": ""}]},
    {"name": "w", "steps": [{"type": "send", "to": "+15551234567"}]},
    {"name": "w", "steps": [{"type": "wait", "seconds": -1}]},
    {"name": "w", "steps": [{"type": "wait", "seconds": 90000}]},
    {"name": "w", "steps": [{"type": "wait", "seconds": True}]},
    {"name": "w", "steps": [{"type": "wait", "seconds": 1, "extra": 2}]},
    {"name": "w", "steps": [{"type": "send", "to": "+15551234567", "body": "x",
                             "when": {"var": "a"}}]},
    {"name": "w", "steps": [{"type": "send", "to": "+15551234567", "body": "x",
                             "when": {"var": "1bad", "equals": 1}}]},
    "not a dict",
    "not json{",
])
def test_invalid_workflows(bad):
    with pytest.raises(WorkflowError):
        parse_workflow(bad)


def test_too_many_steps():
    with pytest.raises(WorkflowError, match="too many steps"):
        parse_workflow({"name": "w", "steps": [{"type": "wait", "seconds": 0}] * 101})


def test_guards_parse():
    wf = parse_workflow({"name": "w", "steps": [
        {"type": "wait", "seconds": 1, "when": {"var": "skip", "equals": True}}]})
    assert wf.steps[0].when == ("skip", True) or wf.steps[0].when.var == "skip"


def test_render():
    assert render("hi {name}!", {"name": "Ada"}) == "hi Ada!"
    with pytest.raises(TemplateError, match="missing variable 'n'"):
        render("hi {n}", {})
    with pytest.raises(TemplateError, match="missing variable 'x'"):
        render("a{x}b{y}", {"y": 1})


def test_render_leaves_non_template_braces():
    assert render("set {a} = {b}", {"a": 1, "b": 2}) == "set 1 = 2"
    assert render("json {\"k\": 1}", {}) == "json {\"k\": 1}"
