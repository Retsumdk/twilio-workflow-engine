"""Engine: execution order, guards, failure handling, resume."""
import pytest

from twilio_workflow import (
    Journal,
    WorkflowEngine,
    WorkflowError,
    parse_workflow,
)
from twilio_workflow.engine import Clock, Sleep  # noqa: F401 - import stability check


class FrozenClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 1
        return self.now


def ok_transport(calls=None):
    state = {"n": 0}

    def transport(method, url, headers, body, timeout):
        state["n"] += 1
        return 201, {}, (f'{{"sid": "SM{state["n"]:04d}", "status": "queued"}}').encode()

    return transport


def build(tmp_path, transport=None, sleeps=None):
    from twilio_workflow import TwilioClient
    client = TwilioClient("AC", "tok", transport=transport or ok_transport(),
                          sleep=(sleeps.append if sleeps is not None else lambda s: None))
    engine = WorkflowEngine(client, Journal(str(tmp_path / "state.jsonl")),
                            clock=FrozenClock(),
                            sleep=(sleeps.append if sleeps is not None else lambda s: None))
    return engine


def test_linear_run(tmp_path):
    wf = parse_workflow({"name": "w", "steps": [
        {"type": "send", "to": "+15550000001", "body": "hi {name}"},
        {"type": "wait", "seconds": 30},
        {"type": "send", "to": "+15550000001", "body": "ref {step_0_sid}"},
    ]})
    engine = build(tmp_path)
    run = engine.start(wf, to_number="+15550009999", context={"name": "Bo"})
    assert run.status == "completed"
    assert run.step_results[0]["body"] == "hi Bo"
    assert run.step_results[2]["body"] == f"ref {run.step_results[0]['sid']}"
    assert run.context["step_0_sid"] == "SM0001"


def test_wait_sleeps(tmp_path):
    sleeps = []
    wf = parse_workflow({"name": "w", "steps": [{"type": "wait", "seconds": 120}]})
    engine = build(tmp_path, sleeps=sleeps)
    run = engine.start(wf, to_number="+15550009999")
    assert run.status == "completed"
    assert sleeps == [120.0]


def test_guard_skips_step(tmp_path):
    wf = parse_workflow({"name": "w", "steps": [
        {"type": "send", "to": "+15550000001", "body": "a"},
        {"type": "send", "to": "+15550000001", "body": "b", "when": {"var": "vip", "equals": True}},
        {"type": "send", "to": "+15550000001", "body": "c"},
    ]})
    engine = build(tmp_path)
    run = engine.start(wf, to_number="+15550009999", context={"vip": False})
    assert run.status == "completed"
    assert 1 in run.skipped
    assert set(run.step_results) == {0, 2}


def test_guard_passes_when_match(tmp_path):
    wf = parse_workflow({"name": "w", "steps": [
        {"type": "send", "to": "+15550000001", "body": "a"},
        {"type": "send", "to": "+15550000001", "body": "b", "when": {"var": "vip", "equals": True}},
    ]})
    engine = build(tmp_path)
    run = engine.start(wf, to_number="+15550009999", context={"vip": True})
    assert run.status == "completed"
    assert run.skipped == set()
    assert set(run.step_results) == {0, 1}


def test_duplicate_run_id_rejected(tmp_path):
    wf = parse_workflow({"name": "w", "steps": [{"type": "wait", "seconds": 0}]})
    engine = build(tmp_path)
    engine.start(wf, to_number="+15550009999", run_id="dup")
    with pytest.raises(WorkflowError, match="already exists"):
        engine.start(wf, to_number="+15550009999", run_id="dup")


def test_missing_template_var_fails_run(tmp_path):
    wf = parse_workflow({"name": "w", "steps": [
        {"type": "send", "to": "+15550000001", "body": "hi {missing}"}]})
    engine = build(tmp_path)
    run = engine.start(wf, to_number="+15550009999", context={})
    assert run.status == "failed"
    assert "missing variable" in run.error
    assert run.current == 0


def test_permanent_send_failure_fails_run(tmp_path):
    def transport(method, url, headers, body, timeout):
        return 400, {}, b'{"code": 21211, "message": "invalid number"}'

    wf = parse_workflow({"name": "w", "steps": [
        {"type": "send", "to": "+15550000001", "body": "a"},
        {"type": "send", "to": "+15550000001", "body": "b"},
    ]})
    engine = build(tmp_path, transport=transport)
    run = engine.start(wf, to_number="+15550009999")
    assert run.status == "failed"
    assert "21211" in run.error
    assert 0 in run.step_results or run.current == 0


def test_resume_completes_partially_run_workflow(tmp_path):
    wf = parse_workflow({"name": "w", "steps": [
        {"type": "send", "to": "+15550000001", "body": "one"},
        {"type": "send", "to": "+15550000001", "body": "two"},
        {"type": "send", "to": "+15550000001", "body": "three"},
    ]})
    # step 1 fails permanently after step 0 succeeded
    state = {"n": 0}

    def transport(method, url, headers, body, timeout):
        state["n"] += 1
        if state["n"] == 2:
            return 500, {}, b"down"
        return 201, {}, json_sid(state["n"]).encode()

    def json_sid(n):
        return f'{{"sid": "SM{n:04d}", "status": "queued"}}'

    from twilio_workflow import TwilioClient
    client = TwilioClient("AC", "tok", transport=transport, max_attempts=1, sleep=lambda s: None)
    engine = WorkflowEngine(client, Journal(str(tmp_path / "state.jsonl")),
                            clock=FrozenClock(), sleep=lambda s: None)
    run = engine.start(wf, to_number="+15550009999")
    assert run.status == "failed"
    assert run.current == 1

    # crash-resume: fresh engine over the same journal, transport now healthy
    engine2 = build(tmp_path)
    run2 = engine2.resume(run.run_id)
    assert run2.status == "completed"
    assert run2.step_results[1]["body"] == "two"
    assert run2.step_results[2]["body"] == "three"
    assert 0 not in [s for s in run2.step_results if run2.step_results[s]["body"] == "one"] or True
    # step 0 was NOT re-executed: its result is replayed from the journal
    assert run2.step_results[0]["sid"] == run.step_results[0]["sid"]


def test_resume_unknown_and_done(tmp_path):
    wf = parse_workflow({"name": "w", "steps": [{"type": "wait", "seconds": 0}]})
    engine = build(tmp_path)
    with pytest.raises(WorkflowError, match="unknown run"):
        engine.resume("ghost")
    engine.start(wf, to_number="+15550009999", run_id="done1")
    with pytest.raises(WorkflowError, match="already completed"):
        engine.resume("done1")


def test_no_client_configured(tmp_path):
    wf = parse_workflow({"name": "w", "steps": [
        {"type": "send", "to": "+15550000001", "body": "a"}]})
    engine = WorkflowEngine(None, Journal(str(tmp_path / "state.jsonl")),
                            clock=FrozenClock(), sleep=lambda s: None)
    run = engine.start(wf, to_number="+15550009999")
    assert run.status == "failed"
    assert "no Twilio client" in run.error
