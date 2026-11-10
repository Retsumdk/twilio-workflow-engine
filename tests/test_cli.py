"""CLI: exit codes, output, resume flow, verify."""
import json

import pytest

from twilio_workflow import cli


class FrozenClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 1
        return self.now


def ok_transport():
    state = {"n": 0}

    def transport(method, url, headers, body, timeout):
        state["n"] += 1
        return 201, {}, json.dumps({"sid": f"SM{state['n']:04d}", "status": "queued"}).encode()

    return transport


@pytest.fixture
def patch(monkeypatch, tmp_path):
    """Point the CLI at a scripted transport and a private journal."""
    from twilio_workflow import TwilioClient
    from twilio_workflow import engine as engine_mod

    def fake_client(*args, **kwargs):
        kwargs.setdefault("transport", ok_transport())
        return TwilioClient("AC", "tok", **kwargs)

    monkeypatch.setattr(cli, "TwilioClient", fake_client)
    monkeypatch.setattr(cli, "load_config", lambda path=None: type("C", (), {
        "account_sid": "AC", "auth_token": "tok", "timeout": 1.0, "max_attempts": 2})())
    real_engine = engine_mod.WorkflowEngine

    def fake_engine(client, journal, clock=None, sleep=None):
        return real_engine(client, journal, clock=FrozenClock(), sleep=(lambda s: None))

    monkeypatch.setattr(cli, "WorkflowEngine", fake_engine)
    return tmp_path


def write_wf(tmp_path, **overrides):
    wf = {"name": "cli-wf", "steps": [
        {"type": "send", "to": "+15550000001", "body": "hi {name}"},
        {"type": "wait", "seconds": 5},
    ]}
    wf.update(overrides)
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(wf))
    return path


def test_validate_ok(patch, capsys):
    rc = cli.main(["validate", str(write_wf(patch))])
    assert rc == 0
    assert "cli-wf (2 steps: 1 send, 1 wait)" in capsys.readouterr().out


def test_validate_bad_exit_2(patch, tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"name": "x", "steps": [{"type": "explode"}]}))
    rc = cli.main(["validate", str(path)])
    assert rc == 2
    assert "unknown type" in capsys.readouterr().err


def test_run_and_status(patch, capsys):
    state = patch / "state.jsonl"
    rc = cli.main(["run", str(write_wf(patch)), "--to", "+15550009999",
                   "--state", str(state), "--context", '{"name": "Ada"}'])
    assert rc == 0
    out = capsys.readouterr().out
    assert "run run-" in out and "sent sid=SM0001" in out and "waited 5s" in out
    rc = cli.main(["status", "--state", str(state)])
    assert rc == 0
    assert "completed" in capsys.readouterr().out


def test_run_bad_context_exit_1(patch, capsys):
    rc = cli.main(["run", str(write_wf(patch)), "--to", "+15550009999",
                   "--state", str(patch / "s.jsonl"), "--context", "not-json"])
    assert rc == 1
    assert "--context" in capsys.readouterr().err


def test_run_missing_template_exit_1(patch, capsys):
    rc = cli.main(["run", str(write_wf(patch)), "--to", "+15550009999",
                   "--state", str(patch / "s.jsonl"), "--context", "{}"])
    assert rc == 1
    assert "missing variable" in capsys.readouterr().err


def test_resume_nothing_exit_2(patch, capsys):
    rc = cli.main(["resume", "--state", str(patch / "empty.jsonl")])
    assert rc == 2
    assert "nothing to resume" in capsys.readouterr().out


def test_resume_flow(patch, tmp_path, capsys, monkeypatch):
    """A run that failed mid-way is resumed by a fresh CLI invocation."""
    import twilio_workflow as pkg

    state = tmp_path / "state.jsonl"
    failing = {"n": 0}

    def flaky_transport(method, url, headers, body, timeout):
        failing["n"] += 1
        if failing["n"] == 2:
            return 500, {}, b"down"
        return 201, {}, json.dumps({"sid": f"SM{failing['n']:04d}", "status": "queued"}).encode()

    from twilio_workflow import TwilioClient
    from twilio_workflow import engine as engine_mod
    real_engine = engine_mod.WorkflowEngine

    monkeypatch.setattr(pkg, "TwilioClient",
                        lambda *a, **k: TwilioClient("AC", "tok", transport=flaky_transport,
                                                     max_attempts=1, sleep=lambda s: None))
    monkeypatch.setattr(cli, "TwilioClient", pkg.TwilioClient)
    monkeypatch.setattr(cli, "load_config", lambda path=None: type("C", (), {
        "account_sid": "AC", "auth_token": "tok", "timeout": 1.0, "max_attempts": 1})())

    def engine_factory(client, journal, clock=None, sleep=None):
        # first call uses the flaky transport; later calls a healthy one
        transport = flaky_transport if client is not None and failing["n"] < 3 else ok_transport()
        client = TwilioClient("AC", "tok", transport=transport,
                              max_attempts=1, sleep=lambda s: None)
        return real_engine(client, journal, clock=FrozenClock(), sleep=(lambda s: None))

    monkeypatch.setattr(cli, "WorkflowEngine", engine_factory)

    wf = tmp_path / "wf3.json"
    wf.write_text(json.dumps({"name": "w3", "steps": [
        {"type": "send", "to": "+15550000001", "body": "one"},
        {"type": "send", "to": "+15550000001", "body": "two"},
        {"type": "send", "to": "+15550000001", "body": "three"},
    ]}))
    rc = cli.main(["run", str(wf), "--to", "+15550009999", "--state", str(state)])
    assert rc == 1  # failed mid-run
    failing["n"] = 10  # transport healthy again
    rc = cli.main(["resume", "--state", str(state)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "completed" in out and "SM0002" in out


def test_verify_ok_and_tampered(patch, tmp_path, capsys):
    state = tmp_path / "state.jsonl"
    rc = cli.main(["run", str(write_wf(patch)), "--to", "+15550009999",
                   "--state", str(state), "--context", '{"name": "x"}'])
    assert rc == 0
    assert cli.main(["verify", "--state", str(state)]) == 0
    assert "journal intact" in capsys.readouterr().out
    lines = state.read_text().splitlines()
    record = json.loads(lines[0])
    record["record"]["ts"] = 1.23
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    state.write_text("\n".join(lines) + "\n")
    assert cli.main(["verify", "--state", str(state)]) == 1
    assert "hash mismatch" in capsys.readouterr().err


def test_no_args_exit_2(patch):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
