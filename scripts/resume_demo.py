"""Interruption/resume demo (PLAN §5 M7; executed in the first minutes of M8 with the screen recording running).

    start `enrich` → wait for N succeeded A3 batches → Ctrl-C (Ctrl-Break on Windows) → checkpoint_before.json
    → `status` → `resume` → wait for M succeeded A3 batches in the resume run → checkpoint_after.json
    → the checker's resume rules locally (pipeline/recovery.py) → [wait for the resume run to finish → check again]

M8 (paid; Rory's "yes" first — the resume run IS the rest of the main run):
    .venv/Scripts/python scripts/resume_demo.py --out evals/recovery \\
        --enrich-args "--provider celeris --escalation anthropic --bucket decisions --workers 4 --paid --max-spend X"

Rehearsal on the FakeProvider (synthetic SYNTH- corpus in its own DB, free):
    .venv/Scripts/python scripts/resume_demo.py --rehearse 2000 --out evals/recovery/rehearsal

`--resume-args` defaults to the enrich args without --scope/--addon-size (resume keeps the stopped run's scope).
"""
import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import recovery, synthetic  # noqa: E402
from pipeline.db.store import connect, now_iso  # noqa: E402

PY = sys.executable
WINDOWS = os.name == "nt"


def say(log, msg, **data):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print("\n" + "=" * 8 + " " + line + " " + "=" * 8, flush=True)
    log.write(json.dumps({"at": now_iso(), "msg": msg, **data}) + "\n")
    log.flush()


def spawn(cmd, log_path):
    """Start a CLI child in its own process group (so only it gets the interrupt) and tee its output."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=flags, start_new_session=not WINDOWS,
                            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    out = open(log_path, "w", encoding="utf-8", newline="\n")

    def pump():
        for line in proc.stdout:
            sys.stdout.write("    | " + line)
            out.write(line)
        out.close()

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    proc.pump = t
    return proc


def interrupt(proc):
    if WINDOWS:
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)  # the CLI maps Ctrl-Break to Ctrl-C (pipeline.cli.ctrl_break_as_ctrl_c)
    else:
        proc.send_signal(signal.SIGINT)


def latest_run(conn):
    row = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    return row[0] if row else None


def wait_run(conn, proc, previous, timeout):
    """The run_id the child created (a new latest run)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        run_id = latest_run(conn)
        if run_id and run_id != previous:
            return run_id
        if proc.poll() is not None:
            raise SystemExit(f"the child exited ({proc.returncode}) before starting a run")
        time.sleep(0.5)
    raise SystemExit("timed out waiting for the run to start")


def a3_batches(conn, run_id):
    return conn.execute("SELECT COUNT(*) FROM calls WHERE run_id=? AND agent='A3' AND outcome='succeeded'",
                        (run_id,)).fetchone()[0]


def n_completed(conn):
    return conn.execute("SELECT COUNT(*) FROM records WHERE status='completed'").fetchone()[0]


def wait_batches(conn, proc, run_id, n, timeout, completed_from):
    """N succeeded A3 calls AND newly completed records (an A3 success alone isn't a committed batch: A5 may be in flight)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if a3_batches(conn, run_id) >= n and n_completed(conn) > completed_from:
            return
        if proc.poll() is not None:
            raise SystemExit(f"the child exited ({proc.returncode}) after {a3_batches(conn, run_id)} batches")
        time.sleep(0.5)
    raise SystemExit(f"timed out: {a3_batches(conn, run_id)} of {n} batches")


def run_info(conn, run_id):
    cfg, phase, reason = conn.execute("SELECT config_json, phase, stop_reason FROM runs WHERE run_id=?",
                                      (run_id,)).fetchone()
    calls = dict(conn.execute("SELECT outcome, COUNT(*) FROM calls WHERE run_id=? GROUP BY 1", (run_id,)).fetchall())
    return {"run_id": run_id, "phase": phase, "stop_reason": reason, "label_config": json.loads(cfg)["label_config"],
            "calls": calls}


def strip_scope(args):
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("--scope", "--addon-size"):
            skip = True
            continue
        if a.startswith(("--scope=", "--addon-size=")):
            continue
        out.append(a)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=Path("state/pipeline.db"))
    p.add_argument("--root", type=Path, default=Path("."), help="the CLI's --root (outputs/run_log.jsonl)")
    p.add_argument("--out", type=Path, default=Path("evals/recovery"))
    p.add_argument("--enrich-args", default="", help="flags for `pipeline.cli enrich` (one string)")
    p.add_argument("--resume-args", default=None, help="flags for `pipeline.cli resume` (default: the enrich args)")
    p.add_argument("--batches", type=int, default=3, help="succeeded A3 calls (+ ≥ 1 committed batch) before the interrupt")
    p.add_argument("--resume-batches", type=int, default=3, help="the same in the resume run before 'after'")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds per wait")
    p.add_argument("--rehearse", type=int, default=0, help="N synthetic rows on the FakeProvider (own DB, free)")
    args = p.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    enrich_args = shlex.split(args.enrich_args, posix=True)
    if args.rehearse:
        args.db = Path("state/m7_rehearsal.db") if args.db == Path("state/pipeline.db") else args.db
        for f in (args.db, Path(str(args.db) + "-wal"), Path(str(args.db) + "-shm")):
            f.unlink(missing_ok=True)
        synthetic.build_db(args.db, synthetic.default_texts(args.rehearse))
        enrich_args = enrich_args or ["--provider", "fake", "--workers", "2", "--fake-latency", "0.4"]
        args.root = args.out
    resume_args = shlex.split(args.resume_args, posix=True) if args.resume_args is not None else strip_scope(enrich_args)
    common = ["--db", str(args.db), "--root", str(args.root)]
    log = open(args.out / "demo_log.jsonl", "w", encoding="utf-8", newline="\n")
    conn = connect(args.db)

    say(log, f"1/7 start enrich (phase initial) on {args.db}", args=enrich_args)
    previous, start_completed = latest_run(conn), n_completed(conn)
    proc = spawn([PY, "-m", "pipeline.cli", "enrich", *common, *enrich_args], args.out / "enrich_initial.log")
    run1 = wait_run(conn, proc, previous, args.timeout)
    wait_batches(conn, proc, run1, args.batches, args.timeout, start_completed)
    say(log, f"2/7 {a3_batches(conn, run1)} A3 batches done in {run1}: sending the interrupt", run_id=run1)
    interrupt(proc)
    try:
        code = proc.wait(timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait()
        say(log, "the run did not stop within 180 s: killed hard (resume recovers it once its lease expires)")
    proc.pump.join(timeout=10)
    info1 = run_info(conn, run1)
    say(log, f"   enrich exited with code {code}; stop_reason={info1['stop_reason']}", exit_code=code, run=info1)
    cfg = info1["label_config"]
    before = list(recovery.completed(conn, cfg))
    recovery.write_checkpoint_file(args.out / "checkpoint_before.json", before, label_config=cfg, run_id=run1,
                                   stop_reason=info1["stop_reason"], db=str(args.db))
    say(log, f"3/7 checkpoint_before.json: {len(before)} completed IDs", count=len(before))

    say(log, "4/7 status")
    status = subprocess.run([PY, "-m", "pipeline.cli", "status", "--db", str(args.db)], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    print(status.stdout)
    (args.out / "status_after_interrupt.txt").write_text(status.stdout, encoding="utf-8", newline="\n")

    say(log, "5/7 resume (phase resume)", args=resume_args)
    mid_completed = n_completed(conn)
    proc = spawn([PY, "-m", "pipeline.cli", "resume", *common, *resume_args], args.out / "resume.log")
    run2 = wait_run(conn, proc, run1, args.timeout)
    wait_batches(conn, proc, run2, args.resume_batches, args.timeout, mid_completed)
    after = list(recovery.completed(conn, cfg))
    recovery.write_checkpoint_file(args.out / "checkpoint_after.json", after, label_config=cfg, run_id=run2,
                                   resumed_from=run1, db=str(args.db))
    say(log, f"6/7 checkpoint_after.json: {len(after)} completed IDs (+{len(after) - len(before)})", count=len(after))

    check = recovery.resume_rules(conn, before, after)
    say(log, f"7/7 checker resume rules: {'PASS' if check['ok'] else 'FAIL ' + ', '.join(check['flags'])}", check=check)
    result = {"db": str(args.db), "label_config": cfg, "initial_run": info1, "resume_run": run2, "check_at_after": check}
    # the resume run is the rest of the main run: keep its output on screen until it ends
    say(log, "waiting for the resume run to finish (Ctrl-C here is forwarded: the run stops cleanly; `resume` again)")
    try:
        code2 = proc.wait()
    except KeyboardInterrupt:
        say(log, "Ctrl-C: forwarding the interrupt to the resume run")
        interrupt(proc)
        code2 = proc.wait()
    proc.pump.join(timeout=10)
    result["resume_run"] = {**run_info(conn, run2), "exit_code": code2}
    result["check_at_end"] = recovery.resume_rules(conn, before, after)
    say(log, f"resume finished ({code2}); rules at the end: "
             f"{'PASS' if result['check_at_end']['ok'] else 'FAIL'}", check=result["check_at_end"])
    (args.out / "resume_check.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    log.close()
    final = result.get("check_at_end", check)
    return 0 if check["ok"] and final["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
