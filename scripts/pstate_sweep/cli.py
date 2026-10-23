# 结构: vibe-scripts/toolkit; CLI -> runtime -> pure core and IO adapters.
# 用途: pstate 内部后台入口，默认禁用，显式切换后才可运行。
# 用法: pstate sweep status --json | run --no-judge --dry-run --json
# 原始需求: 一个产品中联合事项与来源提醒，不自动迁移或启用。
import argparse
import contextlib
import io
import os
import json
import sys
from pathlib import Path

from pstate_threads.cli import Parser, common, envelope, machine_args, read_input
from pstate_threads.core import ThreadError
from . import config, runtime, migration, review
from .adapters import claude_judge, docgate, store, schtask

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "sweep.toml"


def ai_help():
    return """---
name: pstate-sweep
description: Internal discovery backend and unified matter/source review pages.
ai_help_version: 1
---

## Quick Reference
- `pstate sweep status --json`
- `pstate sweep run --no-judge --dry-run --json`
Use subprocess.run([python_path, script_path, "sweep", "status", "--json"]) with list argv.

## When to Use
Inspect or refresh the unified review pages. New installations are disabled until explicit ownership switch.

## Command Reference
status: optional --config PATH; reads PSTATE_HOME/sweep/sources.json.
review: required --input-file PATH or - (stdin); optional --dry-run.
Frozen JSON requires platform/session/review_version/files/items/complete. files maps platforms to explicit
native JSONL paths. items contain payload (original thread add JSON), optional into/rev or explicit distinct.
All input and original excerpts/versions are checked before writes. Each item is saved and independently read back.
Only complete=true plus every verified save permits seen last. Partial association stays unreviewed.
On failure inspect saved IDs and retry the identical frozen input_hash; do not rename, split again or infer distinct.
review never calls a model or records lifecycle decisions; source files do not widen human-confirmation trust.
install-task / uninstall-task: optional --dry-run, schedule settings only from sweep.toml.
install rejects worktrees and versioned plugin caches even in dry-run. Real commands modify Windows tasks;
installation alone never enables the writer. The plaintext run_hidden.pyw uses fixed absolute Python and returns its exit code.
Hidden launches save best-effort diagnostics at PSTATE_HOME/sweep/last_launch.log using the same home resolver;
this is separate from business state and does not enable discovery or change the child exit status.
run: optional --config PATH, --out DIR, --max-calls N, --only-mode recap|resume,
--no-judge, --dry-run, --input-file UTF-8 JSON PATH or - for stdin.
Input may specify shared-history roots/files for bounded isolated verification.
--json and --format json are equivalent. --ai-help and --help print documentation.

## Side Effects & Safety
status is read-only. run requires explicit enabled writer ownership before it scans or constructs a model adapter.
State always lives under PSTATE_HOME/sweep; no legacy session-sweep state directory is used.
run writes permanent source summaries, judgments, reminder closures and the configured review pages.
Matter lifecycle remains solely in threads.json; rendering only reads a short-lock snapshot.
Checkboxes close reminders, never decide, complete or review matters. Model calls use isolated safe mode.
--no-judge avoids model calls. --dry-run follows the same IO write gates and never calls a paid model
or persists source/page data; a coordination lock file may be created.
import-legacy / reconcile: required --legacy-state PATH, optional --out DIR, --dry-run.
Only those explicit paths are read. Preserves complete original states/pages; conflicts keep current data.
sources: returns all permanent summaries, verdicts, errors, closures and imported original pages,
including material hidden by the normal AI status/ownership filter.
switch: optional --legacy-state PATH, --out DIR, --dry-run. Explicitly claims the output and enables this home.
Existing pages require an explicit legacy state on first switch. Never steals the old run.lock.
rollback: optional --out DIR, --dry-run. Reconciles current checkboxes and pauses the new writer.
It preserves current pages, threads and summaries; it does not restore the old writer or an old snapshot.
Use switch to resume forward. The old task is never installed/uninstalled by these commands.
Publishing alone never imports or enables anything. --out uses the identical ownership gates.

## Exit Codes
0 success; 1 operational error; 2 invalid input or usage.

## Errors & Recovery
E_VALIDATION: inspect the configuration or input; fix before retrying.
E_IO: unreadable/corrupt source state or pages; repair explicitly, never replace with empty data.
E_PERMISSION: obtain access to the chosen paths. E_EXTERNAL_TOOL: inspect model launch failure.
Disabled run: perform the explicit writer switch workflow before a real run.
Failures return an error envelope on stderr and leave stdout empty in JSON mode.
"""


def parser():
    root = Parser(prog="pstate sweep", description="内部后台与联合回顾页",
                  epilog="LLMs/agents: run 'pstate sweep --ai-help' for usage guidance.")
    common(root)
    root.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in COMMANDS:
        command = sub.add_parser(name)
        common(command)
        command.add_argument("--config", default=argparse.SUPPRESS)
        if name in ("run", "switch", "rollback", "import-legacy", "reconcile"):
            command.add_argument("--out")
            command.add_argument("--dry-run", action="store_true")
        if name in ("switch", "import-legacy", "reconcile"):
            command.add_argument("--legacy-state", required=name in ("import-legacy", "reconcile"))
        if name == "review":
            command.add_argument("--input-file", required=True)
            command.add_argument("--dry-run", action="store_true")
        if name in ("install-task", "uninstall-task"):
            command.add_argument("--dry-run", action="store_true")
        if name == "run":
            command.add_argument("--max-calls", type=int)
            command.add_argument("--only-mode", choices=("recap", "resume"))
            command.add_argument("--no-judge", action="store_true")
            command.add_argument("--input-file")
    return root


def cmd_run(args, home, cfg):
    state_dir = Path(home) / "sweep"
    out_dir = args.out or cfg["vault_dir"]
    runtime.require_enabled(home, state_dir, out_dir)
    options = read_input(args.input_file) if args.input_file else None
    if options is not None and set(options) - {"roots", "files"}:
        raise ThreadError("run input supports only roots/files")
    judge = claude_judge.ClaudeJudge(cfg, scratch_dir=str(state_dir / "scratch"))
    gate_loader = lambda: docgate.load(docgate.locate(cfg["installed_plugins"], cfg["doc_gate"] or None))
    return runtime.run_sweep(cfg, judge, gate_loader, out_dir, state_dir, max_calls=args.max_calls,
        only_mode=args.only_mode, no_judge=args.no_judge, threads_dir=Path(home) / "threads",
        history_input=options, dry_run=args.dry_run)


def cmd_status(args, home, cfg):
    state_dir = Path(home) / "sweep"
    state = store.load_state(state_dir)
    return {"state_dir": str(state_dir), "last_run": state.get("last_run"),
            "sources": len(state["sessions"]), "closures": len(state.get("closures", {})),
            "activation": migration.output.activation(state_dir)}


def cmd_sources(args, home, cfg):
    # Full durable material, including hidden/completed/company/failed/closed sources.
    return store.load_state(Path(home) / "sweep")


def cmd_import(args, home, cfg):
    return migration.import_legacy(cfg, home, args.legacy_state, args.out or cfg["vault_dir"], args.dry_run)


def cmd_switch(args, home, cfg):
    return migration.switch(cfg, home, args.out or cfg["vault_dir"], args.legacy_state, args.dry_run)


def cmd_rollback(args, home, cfg):
    return migration.rollback(cfg, home, args.out or cfg["vault_dir"], args.dry_run)


def cmd_review(args, home, cfg):
    return review.review(home, read_input(args.input_file), args.dry_run)


def cmd_task(args, home, cfg):
    if args.command == "install-task":
        launcher = Path(__file__).resolve().parents[2] / "run_hidden.pyw"
        script = schtask.install_script(cfg["task_name"], cfg["interval_hours"], launcher, schtask.default_pythonw())
    else:
        script = schtask.uninstall_script(cfg["task_name"])
    result = {"command": args.command, "task_name": cfg["task_name"], "script": script, "dry_run": args.dry_run}
    if not args.dry_run:
        try:
            code, output = schtask.run_powershell(script)
        except OSError as exc:
            raise ThreadError("cannot launch ScheduledTasks command", "E_EXTERNAL_TOOL", 1,
                              {"reason": str(exc)}) from exc
        if code:
            raise ThreadError("ScheduledTasks command failed", "E_EXTERNAL_TOOL", 1,
                              {"returncode": code, "output": output[-4000:]})
        result["output"] = output
    return result


COMMANDS = {"run": cmd_run, "status": cmd_status, "sources": cmd_sources,
            "review": cmd_review, "install-task": cmd_task, "uninstall-task": cmd_task,
            "import-legacy": cmd_import, "reconcile": cmd_import,
            "switch": cmd_switch, "rollback": cmd_rollback}


def _main(argv, home):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    before = argv[:argv.index("--")] if "--" in argv else argv
    json_mode = machine_args(argv)
    if "--ai-help" in before:
        print(ai_help())
        return 0
    details = {}
    try:
        args = parser().parse_args(argv)
        cfg = config.load(args.config)
        result = COMMANDS[args.command](args, Path(home), cfg)
        print(json.dumps(envelope(result) if json_mode else result, ensure_ascii=False, indent=2))
        return 0
    except ThreadError as exc:
        code, status, message, details = exc.code, exc.status, str(exc), exc.details
    except config.ConfigError as exc:
        code, status, message = "E_VALIDATION", 2, str(exc)
    except FileNotFoundError as exc:
        code, status, message = "E_NOT_FOUND", 1, str(exc)
    except PermissionError as exc:
        code, status, message = "E_PERMISSION", 1, str(exc)
    except OSError as exc:
        code, status, message = "E_IO", 1, str(exc)
    except (ValueError, UnicodeError) as exc:
        code, status, message = "E_VALIDATION", 2, str(exc)
    except RuntimeError as exc:
        code, status, message = "E_PERMISSION", 1, str(exc)
    except Exception as exc:
        code, status, message = "E_INTERNAL", 1, str(exc)
    error = {"code": code, "message": message, "details": details, "retryable": False,
             "suggestion": "Inspect status, configuration and writer ownership before retrying."}
    print(json.dumps(envelope(error=error), ensure_ascii=False) if json_mode else message, file=sys.stderr)
    return status


def main(argv, home):
    if os.environ.get("PSTATE_SWEEP_HIDDEN_LAUNCH") != "1":
        return _main(argv, home)
    captured_out, captured_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
        try:
            status = _main(argv, home)
        except SystemExit as exc:
            status = exc.code if isinstance(exc.code, int) else 1
    stdout, stderr = captured_out.getvalue(), captured_err.getvalue()
    try:
        store.write_launch_log(Path(home) / "sweep", status, stdout, stderr)
    except OSError:
        pass
    for stream, text in ((sys.stdout, stdout), (sys.stderr, stderr)):
        if stream is not None:
            stream.write(text)
    return status
