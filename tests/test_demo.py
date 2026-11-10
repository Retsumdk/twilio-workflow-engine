"""Demo output is deterministic (byte-identical) and matches the committed snapshot."""
from pathlib import Path

from twilio_workflow.demo import run_demo

EXPECTED = Path(__file__).resolve().parents[1] / "demo-expected.txt"


def test_demo_deterministic():
    assert run_demo() == run_demo()


def test_demo_matches_committed_snapshot():
    assert run_demo() + "\n" == EXPECTED.read_text()
