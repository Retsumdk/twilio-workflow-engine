# twilio-workflow-engine

Declarative, resumable Twilio SMS workflows with a tamper-evident journal.

## Problem

Twilio messaging automations are usually written as ad-hoc scripts: a loop of
`client.messages.create(...)` calls with sleeps sprinkled in. They have three
recurring failure modes:

- **A crash mid-sequence loses your place.** If the process dies after sending
  step 2 of 5, you either re-send everything or rebuild state by hand.
- **No record of what actually happened.** When someone asks "did the customer
  get the follow-up?", the answer lives in `print` statements.
- **Retry logic is copy-pasted.** Rate limits (429), 5xx blips, and network
  errors each get their own hand-rolled `try/except` — or, worse, permanent
  errors get retried forever.

## Solution

`twilio-workflow-engine` treats an SMS sequence as a **declared workflow** — a
JSON document — and runs it against three primitives:

1. **Validated step list** — `send` and `wait` steps with `{variable}`
   templates and optional `when` guards, checked before anything is sent
   (unknown step types, malformed numbers, missing template variables are
   rejected up front, not mid-run).
2. **Hash-chained journal** — every event (run started, step completed, run
   failed) is appended to a JSONL file as a SHA-256 hash chain. Run state is
   rebuilt by replaying the journal, so a crashed process resumes exactly
   where it stopped, and `twe verify` proves the file was not edited.
3. **Honest Twilio client** — stdlib-only REST calls with full-jitter retry on
   429/5xx/network errors (honoring `Retry-After`), and **immediate**
   surfacing of permanent errors (401, 403, invalid number…). A failed run
   stops, is journaled, and can be resumed later — `twe resume` never re-sends
   completed steps.

Compared with a hand-written script: the state machine, retry policy, and
audit trail are the library's job; your code is the workflow definition.

## How it works

```
workflow JSON ──parse/validate──▶ Workflow ─┐
                                            ├─▶ WorkflowEngine ─▶ Journal (hash-chained JSONL)
TwilioClient (injectable transport) ────────┘        │
                                                     └─▶ run states: running → completed | failed
```

- **Steps** — `send` (E.164 `to`, `{var}`-templated `body`) and `wait`
  (`seconds`, capped at 24 h). Every step accepts an optional
  `when: {var, equals}` guard; a false guard records the step as `skipped`
  instead of executing it.
- **Context** — the run context holds your variables plus
  `step_<n>_sid` / `step_<n>_status` after each send, so later steps can
  reference earlier message SIDs (confirmation links, reference codes).
- **Journal** — each line is `{"format", "version", "hash", "record"}` where
  `hash = sha256(prev_hash | canonical(record))`. Replay rebuilds every run;
  any edit to any record breaks the chain and is reported with a line number.
- **Failure policy** — retryable errors are retried inside the client
  (4 attempts, exponential with full jitter); permanent errors fail the run.
  `resume` continues from the first incomplete step.

## Getting started

Python 3.11+ — zero runtime dependencies, stdlib only.

```bash
git clone https://github.com/Retsumdk/twilio-workflow-engine.git
cd twilio-workflow-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # installs the `twe` CLI and library
twe demo                    # deterministic offline demo, no Twilio account needed
```

Or with dev tools:

```bash
pip install -r requirements-dev.txt   # pytest, ruff, mypy
pytest -q
ruff check src tests && mypy
```

Credentials come from the environment: `TWILIO_ACCOUNT_SID` and
`TWILIO_AUTH_TOKEN`. Optional `TWE_TIMEOUT` (seconds) and `TWE_MAX_ATTEMPTS`
override the defaults, or write them into a JSON config file and pass
`--config`.

## Examples

Declare a workflow:

```json
{
  "name": "renewal-reminder",
  "steps": [
    { "type": "send", "to": "+15552220001",
      "body": "Hi {name}, your renewal is due on {date}." },
    { "type": "wait", "seconds": 3600 },
    { "type": "send", "to": "+15552220001",
      "when": { "var": "step_0_status", "equals": "queued" },
      "body": "Reminder {name}: ref {step_0_sid}" }
  ]
}
```

Validate it (bad numbers are caught before any API call):

```console
$ twe validate wf.json
renewal-reminder (3 steps: 2 send, 1 wait)
$ twe validate bad.json
error: step 0: 'to' must be E.164 (e.g. +15551234567), got '555-1234'
$ echo $?
1
```

Run it against real credentials — here with a deliberately wrong SID so you
can see how a permanent failure is handled (real Twilio response, captured):

```console
$ TWILIO_ACCOUNT_SID=AC00000000000000000000000000000000 \
  TWILIO_AUTH_TOKEN=demo-token twe run wf.json --to +15550001111 \
  --state runs.jsonl --context '{"name": "Ada", "date": "Nov 30"}'
error: twilio API error 401 (code 20003): auth account AC00000000000000000000000000000000 does not exist
run run-84e592fa8574: failed (3 steps, at step 0)
  step 0: pending
  ...
  error: twilio API error 401 (code 20003): auth account AC00000000000000000000000000000000 does not exist
$ echo $?
1
```

The offline demo shows a full successful run (scripted transport, frozen
clock — this exact output is committed as `demo-expected.txt` and diffed in CI):

```console
$ twe demo
twilio-workflow-engine demo
==========================

workflow: renewal-reminder (3 steps: 2 send, 1 wait)

run: demo-run-01
  step 0: send -> Hi Ada, your renewal is due on Nov 30.
  step 1: wait -> waited 60s
  step 2: send -> Reminder Ada: reply DONE to confirm. Ref: SM000000000000000000000001

status: completed, sends: 2, sleeps: [60.0]
final context: {"date": "Nov 30", "name": "Ada", "step_0_sid": "SM000000000000000000000001", ...}
```

Inspect and prove integrity:

```console
$ twe status --state runs.jsonl
run run-84e592fa8574: failed (3 steps, at step 0)
  step 0: pending
  ...
$ twe verify --state runs.jsonl
journal intact
```

After fixing credentials, `twe resume --state runs.jsonl` continues from step 0
(completed steps are never re-sent).

### Library use

```python
from twilio_workflow import Journal, TwilioClient, WorkflowEngine, parse_workflow

client = TwilioClient("ACxxxx", "auth-token")            # real transport
engine = WorkflowEngine(client, Journal("runs.jsonl"))
run = engine.start(
    parse_workflow(open("wf.json").read()),
    to_number="+15550001111",
    context={"name": "Ada", "date": "Nov 30"},
)
print(run.status, run.step_results)
```

Both `TwilioClient` (HTTP transport, sleep, RNG) and `WorkflowEngine` (clock,
sleep) accept injectables, so the entire engine is testable offline.

## CLI

| Command | Purpose | Exit codes |
|---|---|---|
| `twe validate <wf.json>` | Validate a workflow file | 0 ok · 1 error |
| `twe run <wf.json> --to <E.164> --state <file> [--context JSON] [--config file]` | Start a run | 0 ok · 1 error/failed run |
| `twe resume --state <file> [--run <id>]` | Resume first incomplete run | 0 ok · 1 error · 2 nothing to resume |
| `twe status --state <file> [--run <id>]` | Show run state | 0 ok · 1 error |
| `twe verify --state <file>` | Verify journal hash chain | 0 intact · 1 tampered/corrupt |
| `twe demo` | Deterministic offline demo | 0 |

## Design notes

- **Zero runtime dependencies.** The Twilio REST call is ~100 lines of stdlib
  `urllib` — form-encoded POST to `/2010-04-01/Accounts/{sid}/Messages.json`
  with basic auth. No SDK, no vendor lock-in.
- **The journal is the source of truth.** In-memory run objects are
  projections of a replay; nothing lives only in RAM.
- **Permanent vs. retryable is explicit.** `TwilioApiError.retryable` decides
  retry inside the client and stop-the-run at the engine layer; Twilio's own
  error codes are preserved on the exception.
- **Determinism is tested.** The demo runs on a scripted transport and frozen
  clock; CI diffs its output against `demo-expected.txt`.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q          # 67 tests
ruff check src tests
mypy               # strict
```

## License

MIT — see [LICENSE](LICENSE).
