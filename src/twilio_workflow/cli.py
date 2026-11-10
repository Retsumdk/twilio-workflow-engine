"""`twe` CLI: validate/run/resume/status/verify/demo."""
from __future__ import annotations

import argparse
import json
import sys

from .config import ConfigError, load_config
from .demo import run_demo
from .engine import WorkflowEngine
from .errors import StateError, TweError, WorkflowError
from .state import Journal, Run, verify_journal
from .twilio import TwilioApiError, TwilioClient
from .workflow import load_workflow_file

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _print_runs(engine: WorkflowEngine, run_id: str | None) -> int:
    runs = engine.runs()
    if run_id is not None:
        run = runs.get(run_id)
        if run is None:
            print(f"error: unknown run {run_id!r}", file=sys.stderr)
            return EXIT_ERROR
        selected = [run]
    else:
        selected = sorted(runs.values(), key=lambda r: r.created_at)
    for run in selected:
        print(f"run {run.run_id}: {run.status} ({len(run.steps)} steps, at step {run.current})")
        for index in range(len(run.steps)):
            result = run.step_results.get(index)
            if index in run.skipped:
                state = "skipped"
            elif result is None:
                state = "pending"
            elif result["kind"] == "send":
                state = f"sent sid={result['sid']} status={result['status']}"
            else:
                state = f"waited {result['seconds']}s"
            print(f"  step {index}: {state}")
        if run.error:
            print(f"  error: {run.error}")
    return EXIT_OK


def _failure_code(run: Run, rc: int) -> int:
    if run.status == "failed" and run.error:
        print(f"error: {run.error}", file=sys.stderr)
        return EXIT_ERROR
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="twe", description="Twilio workflow engine")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a workflow file")
    validate.add_argument("workflow")

    run_p = sub.add_parser("run", help="start a workflow run")
    run_p.add_argument("workflow")
    run_p.add_argument("--to", required=True, help="sender number (E.164) used as From")
    run_p.add_argument("--state", required=True, help="journal state file")
    run_p.add_argument("--context", default="{}", help="JSON context for templates")
    run_p.add_argument("--config", help="config file (JSON)")

    resume = sub.add_parser("resume", help="resume an incomplete run")
    resume.add_argument("--state", required=True)
    resume.add_argument("--run", help="run id (default: first incomplete run)")
    resume.add_argument("--config")

    status = sub.add_parser("status", help="show run status")
    status.add_argument("--state", required=True)
    status.add_argument("--run")

    verify = sub.add_parser("verify", help="verify journal integrity")
    verify.add_argument("--state", required=True)

    sub.add_parser("demo", help="deterministic offline demo")

    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            workflow = load_workflow_file(args.workflow)
            print(workflow.summary())
            return EXIT_OK

        if args.command == "demo":
            print(run_demo())
            return EXIT_OK

        if args.command == "verify":
            problems = verify_journal(args.state)
            if problems:
                for problem in problems:
                    print(problem, file=sys.stderr)
                return EXIT_ERROR
            print("journal intact")
            return EXIT_OK

        if args.command == "status":
            return _print_runs(WorkflowEngine(None, Journal(args.state)), args.run)

        if args.command == "run":
            try:
                context = json.loads(args.context)
            except json.JSONDecodeError as exc:
                raise WorkflowError(f"--context is not valid JSON: {exc}") from exc
            if not isinstance(context, dict):
                raise WorkflowError("--context must be a JSON object")
            workflow = load_workflow_file(args.workflow)
            cfg = load_config(args.config)
            client = TwilioClient(cfg.account_sid, cfg.auth_token,
                                  timeout=cfg.timeout, max_attempts=cfg.max_attempts)
            engine = WorkflowEngine(client, Journal(args.state))
            run = engine.start(workflow, to_number=args.to, context=context)
            rc = _print_runs(engine, run.run_id)
            return _failure_code(run, rc)

        if args.command == "resume":
            cfg = load_config(args.config)
            client = TwilioClient(cfg.account_sid, cfg.auth_token, timeout=cfg.timeout,
                                   max_attempts=cfg.max_attempts)
            engine = WorkflowEngine(client, Journal(args.state))
            runs = engine.runs()
            run_id = args.run
            if run_id is None:
                incomplete = [r for r in runs.values() if r.status in ("running", "failed")]
                if not incomplete:
                    print("nothing to resume")
                    return EXIT_USAGE
                incomplete.sort(key=lambda r: r.created_at)
                run_id = incomplete[0].run_id
            client = TwilioClient(cfg.account_sid, cfg.auth_token)
            engine = WorkflowEngine(client, Journal(args.state))
            run = engine.resume(run_id)
            rc = _print_runs(engine, run.run_id)
            return _failure_code(run, rc)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE if args.command == "validate" else EXIT_ERROR
    except WorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE if args.command == "validate" else EXIT_ERROR
    except (StateError, TwilioApiError, TweError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    raise AssertionError("unreachable command")  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
