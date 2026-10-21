#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run the full test suite and write a combined result log to
tests/results/last_run.txt.

Suites:
    A: real file regression
    B: merges / nested tables
    C: shapes / text boxes
    D: review-fix regression guards (in-cell shape, borderFill bg,
       to_matrix out-of-range origin, addr fallback, robustness)

Run:  python tests/run_all.py
Exit code mirrors overall pass/fail.
"""

import io
import os
import sys
import contextlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import test_real_file  # noqa: E402
import test_merge_and_nested  # noqa: E402
import test_shapes  # noqa: E402
import test_fixes  # noqa: E402

RESULTS_DIR = os.path.join(_HERE, "results")


def _run(name, fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn()
    text = buf.getvalue()
    print(f"\n########## {name} (exit={rc}) ##########")
    print(text)
    return rc, text


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rc_a, out_a = _run("TEST A: real file regression", test_real_file.main)
    rc_b, out_b = _run("TEST B: merge + nested", test_merge_and_nested.main)
    rc_c, out_c = _run("TEST C: shapes + text boxes", test_shapes.main)
    rc_d, out_d = _run("TEST D: review-fix regression guards", test_fixes.main)

    # A real-file test that returns 0 while emitting "SKIP" was skipped (its
    # private fixture is unavailable), not passed. Skips are NOT failures, so the
    # suite still passes; we only annotate the banner for clarity.
    a_skipped = (rc_a == 0) and ("SKIP" in out_a)

    overall_ok = (rc_a == 0) and (rc_b == 0) and (rc_c == 0) and (rc_d == 0)
    if overall_ok:
        banner = "ALL TESTS PASSED (A skipped)" if a_skipped else "ALL TESTS PASSED"
    else:
        banner = "SOME TESTS FAILED"
    print(f"\n==================== {banner} ====================")

    log_path = os.path.join(RESULTS_DIR, "last_run.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("TEST A: real file regression (exit=%d)\n" % rc_a)
        fh.write(out_a)
        fh.write("\n\nTEST B: merge + nested (exit=%d)\n" % rc_b)
        fh.write(out_b)
        fh.write("\n\nTEST C: shapes + text boxes (exit=%d)\n" % rc_c)
        fh.write(out_c)
        fh.write("\n\nTEST D: review-fix regression guards (exit=%d)\n" % rc_d)
        fh.write(out_d)
        fh.write("\n\n%s\n" % banner)
    print(f"\n[runner] wrote {log_path}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
