#!/usr/bin/env python3
"""Run the skill's pipeline (clean -> detect -> match -> categorise) on every fixture.

    python3 tests/run_eval.py            # print the results table
    python3 tests/run_eval.py --write    # also refresh the AUTO section of tests/EVAL.md

Exit code is non-zero when fewer than 90% of the diagnostic fixtures pass, so
this doubles as a CI gate for changes to the scripts or the pattern library.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import expected, fixture_text
import ci_doctor
import clean_logs
import detect_stack
import match_patterns

EVAL_MD = Path(__file__).resolve().parent / "EVAL.md"
BEGIN, END = "<!-- AUTO:BEGIN -->", "<!-- AUTO:END -->"
PASS_RATIO = 0.9


def threshold(total: int) -> int:
    return math.ceil(PASS_RATIO * total)


def evaluate() -> tuple:
    entries = match_patterns.load_library()
    rows, passed, total = [], 0, 0
    for name, exp in expected().items():
        if "pattern" not in exp:
            continue
        total += 1
        raw = fixture_text(name)
        digest = clean_logs.digest_text(raw, name)
        md = clean_logs.render_markdown(digest)
        stacks = [s["stack"] for s in detect_stack.detect(Path("/nonexistent"), md)["stacks"]]
        matches = match_patterns.match(md, entries, stacks)
        top = matches[0] if matches else None
        category = ci_doctor.guess_category(md, matches)
        kept = all(s in md for s in exp["must_contain"])
        ok = bool(top and top.entry.id == exp["pattern"] and category == exp["category"] and kept)
        passed += ok
        rows.append(
            f"| `{name}` | {len(clean_logs.split_lines(raw))} → {len(md.splitlines())} | {'✓' if kept else '✗'} | "
            f"{', '.join(stacks) or '-'} | {exp['pattern']} | "
            f"{(top.entry.id + f' ({top.confidence}, {top.score:g})') if top else 'none'} | "
            f"{exp['category']} / {category} | {'✅' if ok else '❌'} |"
        )
    header = [
        f"**Automated result: {passed}/{total} fixtures diagnosed correctly** "
        f"(threshold {threshold(total)}/{total}).",
        "",
        "| Fixture | Raw → digest lines | Root-cause line kept | Stacks (log only) | Expected pattern | Top match | Category exp / got | Result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    return "\n".join(header + rows), passed, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="update the AUTO section in tests/EVAL.md")
    args = ap.parse_args()
    table, passed, total = evaluate()
    print(table)
    if args.write:
        text = EVAL_MD.read_text(encoding="utf-8")
        new = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), f"{BEGIN}\n{table}\n{END}", text, flags=re.S)
        EVAL_MD.write_text(new, encoding="utf-8")
        print(f"\nupdated {EVAL_MD}")
    return 0 if passed >= threshold(total) else 1


if __name__ == "__main__":
    sys.exit(main())
