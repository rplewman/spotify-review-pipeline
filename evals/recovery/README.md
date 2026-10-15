# Recovery evidence (PLAN §5 M7 → executed in M8)

`scripts/resume_demo.py` scripts the whole interruption/resume demo, so M8 only has to start it with the screen recording on:

1. start `pipeline.cli enrich` (phase `initial`) in its own process group, output tee'd to `enrich_initial.log`
2. wait for N succeeded A3 calls **and** at least one committed batch (an A3 success alone is not a batch: A5 may still be in flight)
3. send the interrupt: Ctrl-Break on Windows (the CLI maps it to Ctrl-C, `pipeline.cli.ctrl_break_as_ctrl_c`), SIGINT elsewhere → the run
   releases its leases, closes in-flight attempts as failed (settled at the estimate) and exits 130 with `stop_reason=interrupted`
4. `checkpoint_before.json` = `completed_ids` at the interruption; `status_after_interrupt.txt`
5. `pipeline.cli resume` (phase `resume`), output in `resume.log`
6. after N more succeeded A3 calls and new committed records: `checkpoint_after.json`
7. the checker's resume rules, run locally on the DB (`pipeline/recovery.py::resume_rules`, a copy of `check_submission.audit`'s block):
   before nonempty ⊂ after ⊆ completed; every non-cached before ID in a succeeded `initial` enrich call with its record's label_config;
   ≥ 1 new ID in a succeeded `resume` call; **no `resume` enrich call sends a before ID**. Re-checked when the resume run ends.
   Everything lands in `resume_check.json` and `demo_log.jsonl`.

## Rehearsal (FakeProvider, synthetic SYNTH- corpus, free) — `rehearsal/`

`.venv/Scripts/python scripts/resume_demo.py --rehearse 2000 --out evals/recovery/rehearsal` (2026-10-15):
interrupt after 3 A3 calls → exit 130, `interrupted`, 2 in-flight calls closed as failed, 0 left reserved → **431** completed IDs before →
resume → **631** after (+200) → rules **PASS**; resume drained the queue → rules at the end **PASS**, 0 resume calls with a before ID.
The same flow runs in `tests/test_m7_system.py::test_resume_demo_interrupts_and_resumes_cleanly`; a hard kill (no cleanup) → `resume`
is in `evals/system_tests/` (`hard_kill_recovery`).

## M8 (paid, Rory's "yes" first; the resume run is the rest of the main run)

```
.venv/Scripts/python scripts/resume_demo.py --out evals/recovery --enrich-args "--provider celeris --escalation anthropic --bucket decisions --workers 4 --paid --max-spend X"
```

`--resume-args` defaults to the same flags minus `--scope/--addon-size`. The 8,492 texts already done in M6b are in `before` (checked
read-only 2026-10-15: all non-cached ones are in succeeded `initial` calls with the frozen label_config; 0 unlogged). M10 copies
`checkpoint_before.json` / `checkpoint_after.json` into `grading/`.
