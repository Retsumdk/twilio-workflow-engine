"""Journal: replay, resume state, hash-chain integrity."""
import json

import pytest

from twilio_workflow import Journal, StateError, verify_journal


def write_journal(path):
    journal = Journal(str(path))
    journal.append({"event": "run_started", "run_id": "r1", "workflow_name": "w",
                    "to_number": "+15550001111", "steps": [{"type": "wait", "seconds": 1}],
                    "context": {}, "ts": 1.0})
    journal.append({"event": "step_completed", "run_id": "r1", "index": 0,
                    "result": {"kind": "wait", "seconds": 1}, "ts": 2.0})
    journal.append({"event": "run_completed", "run_id": "r1", "ts": 3.0})
    return journal


def test_replay_rebuilds_run(tmp_path):
    path = tmp_path / "state.jsonl"
    write_journal(path)
    reloaded = Journal(str(path))
    run = reloaded.runs()["r1"]
    assert run.status == "completed"
    assert run.current == 1
    assert run.step_results[0] == {"kind": "wait", "seconds": 1}


def test_crash_resume_state(tmp_path):
    path = tmp_path / "state.jsonl"
    journal = Journal(str(path))
    journal.append({"event": "run_started", "run_id": "r2", "workflow_name": "w",
                    "to_number": "+15550001111", "steps": [{"type": "wait", "seconds": 1}] * 2,
                    "context": {}, "ts": 1.0})
    journal.append({"event": "step_completed", "run_id": "r2", "index": 0,
                    "result": {"kind": "wait", "seconds": 1}, "ts": 2.0})
    reloaded = Journal(str(path))
    run = reloaded.runs()["r2"]
    assert run.status == "running"
    assert run.current == 1


def test_unknown_run_reference_rejected(tmp_path):
    path = tmp_path / "state.jsonl"
    journal = Journal(str(path))
    with pytest.raises(StateError, match="unknown run"):
        journal.append({"event": "step_completed", "run_id": "ghost", "index": 0,
                        "result": {}, "ts": 1.0})


def test_verify_intact(tmp_path):
    path = tmp_path / "state.jsonl"
    write_journal(path)
    assert verify_journal(str(path)) == []


def test_verify_detects_tamper(tmp_path):
    path = tmp_path / "state.jsonl"
    write_journal(path)
    lines = path.read_text().splitlines()
    record = json.loads(lines[1])
    record["record"]["index"] = 99  # flip one field: breaks this hash AND the chain
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    problems = verify_journal(str(path))
    assert any("hash mismatch" in p and ":2:" in p for p in problems)


def test_verify_detects_chain_break(tmp_path):
    """Tamper line 2's content and re-sign it correctly: line 2 verifies,
    but line 3's chain (sealed against the OLD line-2 hash) breaks."""
    import hashlib

    path = tmp_path / "state.jsonl"
    write_journal(path)
    lines = path.read_text().splitlines()
    record = json.loads(lines[1])
    record["record"]["result"]["seconds"] = 999  # tamper the recorded outcome
    payload = json.dumps({"prev": json.loads(lines[0])["hash"], "record": record["record"]},
                         sort_keys=True, separators=(",", ":"))
    record["hash"] = hashlib.sha256(payload.encode()).hexdigest()
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    problems = verify_journal(str(path))
    assert any("hash mismatch" in p and ":2:" not in p and ":3:" in p for p in problems)
    assert not any(":2:" in p for p in problems)  # line 2 itself re-signs clean


def test_verify_reports_invalid_json(tmp_path):
    path = tmp_path / "state.jsonl"
    path.write_text("{broken\n")
    problems = verify_journal(str(path))
    assert any("invalid JSON" in p for p in problems)


def test_verify_missing_file(tmp_path):
    missing = str(tmp_path / "nope.jsonl")
    assert verify_journal(missing) == [f"{missing}: journal does not exist"]


def test_strict_load_rejects_tampered(tmp_path):
    path = tmp_path / "state.jsonl"
    write_journal(path)
    lines = path.read_text().splitlines()
    record = json.loads(lines[0])
    record["record"]["ts"] = 42.0
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(StateError, match="hash mismatch"):
        Journal(str(path))


def test_mkdir_nested(tmp_path):
    journal = Journal(str(tmp_path / "a" / "b" / "state.jsonl"))
    journal.append({"event": "run_started", "run_id": "r9", "workflow_name": "w",
                    "to_number": "+15550001111", "steps": [], "context": {}, "ts": 1.0})
    assert (tmp_path / "a" / "b" / "state.jsonl").exists()
    assert "r9" in Journal(str(tmp_path / "a" / "b" / "state.jsonl")).runs()
