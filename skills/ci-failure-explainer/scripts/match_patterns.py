#!/usr/bin/env python3
"""Match a digest (or log) against the known-error library in references/.

The reference markdown files ARE the pattern database: every entry's
`**Signature:**` field holds one or more backtick-quoted Python regexes. This
script parses those entries, runs the regexes against the digest, and returns
the best-ranked matches together with the entry text (cause / fix / verify), so
the agent reads only the entries that matter instead of whole reference files.

  match_patterns.py digest.md                  # rank matches (markdown)
  match_patterns.py digest.md --format json
  match_patterns.py --list                     # catalogue of all entries
  match_patterns.py --show NODE-01             # print one entry
  match_patterns.py digest.md --explain NODE-01  # why an entry scored what it did, and its rank
  match_patterns.py --validate                 # lint the library (used by tests/CI)

Standard library only. Python 3.9+.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from _version import __version__
from clean_logs import redacted_lines

try:  # the regex parser is stdlib; it moved in Python 3.11
    from re import _constants as _sre
    from re import _parser as _sre_parse
except ImportError:  # Python 3.9 / 3.10
    import sre_constants as _sre  # type: ignore[no-redef]
    import sre_parse as _sre_parse  # type: ignore[no-redef]

SCHEMA_VERSION = 1

REFERENCES_DIR = Path(__file__).resolve().parent.parent / "references"
REQUIRED_FIELDS = ("Signature", "Category", "Cause", "Fix", "Verify")
CATEGORIES = {
    "build", "test", "lint", "dependency", "config", "infrastructure", "flaky", "permissions",
}
ENTRY_HEADING = re.compile(r"^### (?P<id>[A-Z]+-\d{2,3}) · (?P<title>.+?)\s*$")
FIELD_LINE = re.compile(r"^- \*\*(?P<name>[A-Za-z ]+):\*\*\s?(?P<value>.*)$")
BACKTICKS = re.compile(r"`([^`]+)`")
DIGEST_GUTTER = re.compile(r"^[▶ ]\s*\d+ \| (?:\[!injection\?\] )?")
ROOT_CAUSE_HEADER = re.compile(r"^- \*\*Root-cause candidate:\*\* `(.*)` \(line \d+\)")


@dataclass
class Entry:
    id: str
    title: str
    file: str
    stack: str
    fields: Dict[str, str]
    text: str
    line: int
    patterns: List[re.Pattern[str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        return self.fields.get("Category", "").strip().lower()


# --------------------------------------------------------------------------- #
# Catastrophic-backtracking lint. Signatures run on hostile log lines, so a
# nested unbounded repeat such as (\S+\s?)+ could stall a digest for minutes.
# --------------------------------------------------------------------------- #

_REPEATS = (_sre.MAX_REPEAT, _sre.MIN_REPEAT)
_SINGLE = (_sre.LITERAL, _sre.NOT_LITERAL, _sre.ANY, _sre.IN, _sre.CATEGORY)
_CATEGORY_RX = {
    _sre.CATEGORY_DIGIT: re.compile(r"\d"), _sre.CATEGORY_NOT_DIGIT: re.compile(r"\D"),
    _sre.CATEGORY_SPACE: re.compile(r"\s"), _sre.CATEGORY_NOT_SPACE: re.compile(r"\S"),
    _sre.CATEGORY_WORD: re.compile(r"\w"), _sre.CATEGORY_NOT_WORD: re.compile(r"\W"),
}


def _children(op: object, av: object) -> List[list]:
    if op in _REPEATS:
        return [list(av[2])]  # type: ignore[index]
    if op == _sre.SUBPATTERN:
        return [list(av[-1])]  # type: ignore[index]
    if op == _sre.BRANCH:
        return [list(b) for b in av[1]]  # type: ignore[index]
    return []


def _unbounded_bodies(items: list) -> List[list]:
    found = []
    for op, av in items:
        if op in _REPEATS and av[1] == _sre.MAXREPEAT:
            found.append(list(av[2]))
        for child in _children(op, av):
            found += _unbounded_bodies(child)
    return found


def _char_in(node: Tuple[object, object], ch: str) -> bool:
    """Whether a single-character regex node matches ch (unknown shapes: assume yes)."""
    op, av = node
    if op == _sre.LITERAL:
        return ord(ch) == av
    if op == _sre.NOT_LITERAL:
        return ord(ch) != av
    if op == _sre.ANY:
        return ch != "\n"
    if op == _sre.CATEGORY:
        rx = _CATEGORY_RX.get(av)
        return rx is None or bool(rx.match(ch))
    if op == _sre.IN:
        negate = bool(av) and av[0][0] == _sre.NEGATE  # type: ignore[index]
        hit = any(_char_in((o, a), ch) if o != _sre.RANGE else a[0] <= ord(ch) <= a[1]  # type: ignore[index]
                  for o, a in av if o != _sre.NEGATE)  # type: ignore[union-attr]
        return hit != negate
    return True


def _disjoint(sep: Tuple[object, object], body: list) -> bool:
    """True if no ASCII character fits both the separator and the repeated body."""
    if len(body) != 1 or body[0][0] not in _SINGLE:
        return False
    return not any(_char_in(sep, c) and _char_in(body[0], c) for c in map(chr, range(128)))


def ambiguous_repeat(pattern: str) -> bool:
    """Flag an unbounded repeat containing another unbounded repeat with nothing to tell
    iterations apart: (a+)+, (\\S+\\s?)+, (.*,)+. A mandatory separator the inner repeats
    can't consume keeps matching linear, so (\\.\\w+)* and (\\w+\\s)* are fine."""

    def walk(items: list) -> bool:
        for op, av in items:
            if op in _REPEATS and av[1] == _sre.MAXREPEAT:
                body = list(av[2])
                while len(body) == 1 and body[0][0] == _sre.SUBPATTERN:
                    body = list(body[0][1][-1])
                inner = _unbounded_bodies(body)
                seps = [node for node in body if node[0] in _SINGLE]
                if inner and not any(all(_disjoint(s, b) for b in inner) for s in seps):
                    return True
            if any(walk(child) for child in _children(op, av)):
                return True
        return False

    return walk(list(_sre_parse.parse(pattern)))


def stack_of(path: Path) -> str:
    stem = path.stem
    return stem[len("stack-"):] if stem.startswith("stack-") else stem.replace("-tests", "")


def parse_reference(path: Path) -> List[Entry]:
    entries: List[Entry] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        m = ENTRY_HEADING.match(lines[i])
        if not m:
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines) and not re.match(r"^#{2,3} ", lines[i]):
            i += 1
        body = lines[start:i]
        fields: Dict[str, str] = {}
        current = None
        for ln in body[1:]:
            fm = FIELD_LINE.match(ln)
            if fm:
                current = fm.group("name").strip()
                fields[current] = fm.group("value")
            elif current:
                fields[current] += "\n" + ln
        e = Entry(id=m.group("id"), title=m.group("title"), file=path.name, stack=stack_of(path),
                  fields=fields, text="\n".join(body).strip(), line=start + 1)
        for name in REQUIRED_FIELDS:
            if not fields.get(name, "").strip():
                e.errors.append(f"missing **{name}:**")
        for raw in BACKTICKS.findall(fields.get("Signature", "")):
            try:
                rx = re.compile(raw)
            except re.error as exc:
                e.errors.append(f"bad regex {raw!r}: {exc}")
                continue
            if rx.search(""):
                e.errors.append(f"regex matches empty string: {raw!r}")
                continue
            if ambiguous_repeat(raw):
                e.errors.append(f"regex can backtrack catastrophically (nested unbounded repeat): {raw!r}")
                continue
            e.patterns.append(rx)
        if "Signature" in fields and not e.patterns and not e.errors:
            e.errors.append("signature has no backtick-quoted regex")
        if e.category and e.category not in CATEGORIES:
            e.errors.append(f"unknown category {e.category!r} (use one of {sorted(CATEGORIES)})")
        entries.append(e)
    return entries


def load_library(ref_dir: Path = REFERENCES_DIR) -> List[Entry]:
    entries: List[Entry] = []
    for path in sorted(ref_dir.glob("*.md")):
        entries.extend(parse_reference(path))
    return entries


def extract_lines(text: str) -> Tuple[List[str], Optional[str]]:
    """Return (searchable lines, root-cause text). Understands clean_logs digests."""
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
            lines = [ln.get("text", "") for st in data.get("steps", []) for ln in st.get("lines", [])]
            rc = (data.get("root_cause") or {}).get("text")
            return lines, rc
        except ValueError:
            pass
    if text.lstrip().startswith("# CI failure digest"):
        lines, rc, in_code = [], None, False
        for ln in text.splitlines():
            hm = ROOT_CAUSE_HEADER.match(ln)
            if hm:
                rc = hm.group(1)
            if ln.startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                lines.append(DIGEST_GUTTER.sub("", ln))
        return lines, rc
    # Raw log: redact before matching so nothing secret flows into the output.
    return [ln.text for ln in redacted_lines(text)], None


@dataclass
class Match:
    entry: Entry
    score: float
    hits: List[Tuple[str, str]]  # (pattern, matched line)
    on_root_cause: bool
    parts: List[Tuple[str, float]] = field(default_factory=list)  # (reason, points) for --explain

    @property
    def confidence(self) -> str:
        if self.on_root_cause or len(self.hits) >= 2:
            return "high"
        return "medium" if self.hits else "low"


_META = re.compile(r"\\[dDsSwWbB]|\\.|\[[^\]]*\]|\(\?[:=!<]*|[()|^$*+?{}.,]|\d")


def specificity(pattern: str) -> float:
    """0.4..1.2: how much literal text a signature pins down.

    A generic signature such as the Jest "● suite > test" header ("some test failed") should
    count for less than `Exceeded timeout of \\d+ ?ms for a test`, so specific
    explanations outrank symptom-level entries when both match.
    """
    literal = _META.sub("", pattern)
    return max(0.4, min(1.2, len(literal) / 25.0))


def score_entry(e: Entry, lines: Sequence[str], root_cause: Optional[str], stacks: Sequence[str]) -> Match:
    """Score one entry. A Match with no hits and on_root_cause False means "did not match"."""
    hits: List[Tuple[str, str]] = []
    parts: List[Tuple[str, float]] = []
    rc_spec = 0.0
    for rx in e.patterns:
        for ln in lines:
            if rx.search(ln):
                hits.append((rx.pattern, ln.strip()[:240]))
                parts.append((f"signature `{rx.pattern}`", 10.0 * specificity(rx.pattern)))
                break
        if root_cause and rx.search(root_cause):
            rc_spec = max(rc_spec, specificity(rx.pattern))
    on_rc = rc_spec > 0
    if not hits and not on_rc:
        return Match(e, 0.0, [], False)
    if on_rc:
        parts.append(("matches the root-cause line", 20.0 * rc_spec + 5.0))
    if e.stack in {s.lower() for s in stacks}:
        parts.append((f"stack `{e.stack}` detected", 5.0))
    elif e.stack in ("general", "flaky"):
        parts.append((f"`{e.stack}` entries apply to every stack", 2.0))
    return Match(e, round(sum(p for _, p in parts), 1), hits, on_rc, parts)


def match(text: str, entries: Sequence[Entry], stacks: Sequence[str] = (), top: int = 5) -> List[Match]:
    lines, root_cause = extract_lines(text)
    results = [m for e in entries if not e.errors and e.patterns
               for m in [score_entry(e, lines, root_cause, stacks)] if m.hits or m.on_root_cause]
    results.sort(key=lambda m: (-m.score, m.entry.id))
    return results[:top]


def explain(text: str, entries: Sequence[Entry], entry_id: str, stacks: Sequence[str] = ()) -> Optional[str]:
    """Markdown breakdown of one entry's score against the input, or None if the id is unknown."""
    entry = next((e for e in entries if e.id.lower() == entry_id.lower()), None)
    if entry is None:
        return None
    out = [f"## {entry.id} · {entry.title}", ""]
    if entry.errors:
        return "\n".join([*out, "This entry is skipped by the matcher because it is invalid:",
                          *(f"- {err}" for err in entry.errors)])
    lines, root_cause = extract_lines(text)
    m = score_entry(entry, lines, root_cause, stacks)
    hit_lines = dict(m.hits)
    out += ["| Signature | Specificity | Result |", "|---|---:|---|"]
    for rx in entry.patterns:
        spec = specificity(rx.pattern)
        line = hit_lines.get(rx.pattern)
        result = f"matched `{line.replace('`', chr(39))[:120]}` (+{10 * spec:.1f})" if line else "no line matched"
        out.append(f"| `{rx.pattern.replace('|', chr(92) + '|')}` | {spec:.2f} | {result} |")
    out.append("")
    if root_cause is None:
        out.append("- **Root-cause line:** none in the input (pass a clean_logs.py digest to get one)")
    else:
        verdict = "matched" if m.on_root_cause else "not matched"
        out.append(f"- **Root-cause line:** `{root_cause.replace('`', chr(39))[:160]}`: {verdict}")
    out += [f"- {reason}: +{points:.1f}" for reason, points in m.parts if not reason.startswith("signature ")]
    ranked = match(text, entries, stacks, top=len(entries))
    rank = next((i for i, r in enumerate(ranked, 1) if r.entry.id == entry.id), None)
    if rank is None:
        out.append("- **Total:** 0 (no signature matched, so the entry is not ranked)")
    else:
        out.append(f"- **Total:** {m.score:g} · confidence **{m.confidence}** · rank **{rank}** of {len(ranked)}")
    if ranked and ranked[0].entry.id != entry.id:
        out.append(f"- **Top match instead:** {ranked[0].entry.id} · {ranked[0].entry.title} (score {ranked[0].score:g})")
    return "\n".join(out)


def render_markdown(matches: Sequence[Match], with_text: bool = True) -> str:
    if not matches:
        return ("## Known-pattern matches\n\nNo known pattern matched. Follow references/diagnostic-method.md "
                "from step 1 and consider contributing a new entry once solved.")
    out = ["## Known-pattern matches", ""]
    for i, m in enumerate(matches, 1):
        e = m.entry
        out.append(f"{i}. **{e.id} · {e.title}** - {e.category}, confidence **{m.confidence}** "
                   f"(score {m.score:g}{', matches root cause' if m.on_root_cause else ''}) "
                   f"→ `references/{e.file}`")
        for _pattern, line in m.hits[:2]:
            out.append(f"   - matched `{line.replace('`', chr(39))}`")
    if with_text:
        out += ["", "### Best match details", "", matches[0].entry.text]
        if len(matches) > 1 and matches[1].score >= matches[0].score * 0.6:
            out += ["", "### Runner-up details", "", matches[1].entry.text]
    return "\n".join(out)


def render_json(matches: Sequence[Match]) -> str:
    return json.dumps([
        {
            "id": m.entry.id, "title": m.entry.title, "category": m.entry.category,
            "reference": f"references/{m.entry.file}", "line": m.entry.line,
            "score": m.score, "confidence": m.confidence, "on_root_cause": m.on_root_cause,
            "hits": [{"pattern": p, "line": ln} for p, ln in m.hits],
            "cause": m.entry.fields.get("Cause", "").strip(),
            "fix": m.entry.fields.get("Fix", "").strip(),
            "verify": m.entry.fields.get("Verify", "").strip(),
        }
        for m in matches
    ], indent=2, ensure_ascii=False)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Rank known CI failure patterns against a digest or log.")
    ap.add_argument("input", nargs="?", help="digest (markdown/JSON from clean_logs.py) or raw log; - for stdin")
    ap.add_argument("--stacks", default="", help="comma-separated detected stacks to boost (e.g. node,docker)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ap.add_argument("--references", default=str(REFERENCES_DIR), help="references directory")
    ap.add_argument("--list", action="store_true", help="list every entry in the library")
    ap.add_argument("--show", metavar="ID", help="print one entry, e.g. NODE-01")
    ap.add_argument("--explain", metavar="ID", help="show how one entry scores against the input, and its rank")
    ap.add_argument("--validate", action="store_true", help="lint the library; non-zero exit on problems")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

    ref_dir = Path(args.references)
    if not ref_dir.is_dir():
        print(f"error: references directory not found: {ref_dir}", file=sys.stderr)
        return 2
    entries = load_library(ref_dir)

    if args.validate:
        problems = [(e, err) for e in entries for err in e.errors]
        ids: Dict[str, int] = {}
        for e in entries:
            ids[e.id] = ids.get(e.id, 0) + 1
        dupes = [i for i, c in ids.items() if c > 1]
        for e, err in problems:
            print(f"{e.file}:{e.line} {e.id}: {err}")
        for d in dupes:
            print(f"duplicate id: {d}")
        print(f"{len(entries)} entries, {len(problems)} problem(s), {len(dupes)} duplicate id(s)")
        return 1 if problems or dupes else 0
    if args.list:
        for e in entries:
            print(f"{e.id:<10} {e.category:<15} {e.title}  ({e.file})")
        return 0
    if args.show:
        for e in entries:
            if e.id.lower() == args.show.lower():
                print(e.text)
                return 0
        print(f"error: no entry with id {args.show}", file=sys.stderr)
        return 1
    if not args.input:
        ap.error("an input digest/log is required (or use --list / --show / --validate)")
    try:
        text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        return 2
    stacks = [s.strip() for s in args.stacks.split(",") if s.strip()]
    if args.explain:
        report = explain(text, entries, args.explain, stacks)
        if report is None:
            print(f"error: no entry with id {args.explain}", file=sys.stderr)
            return 1
        print(report)
        return 0
    found = match(text, entries, stacks, top=args.top)
    print(render_json(found) if args.format == "json" else render_markdown(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
