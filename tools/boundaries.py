#!/usr/bin/env python3
"""Review boundary PRs (PRs that only change functions.csv) against the maintainer's ROM.

  tools/boundaries.py check REF          review the functions.csv of git REF against origin/main
  tools/boundaries.py routine [--dry-run]
                                         review every open PR that only changes functions.csv;
                                         merge the ones with evidence from trusted authors
                                         (pool.toml `trusted`, plus you), comment on the rest

Needs baserom.gba, so it runs on the maintainer's machine, never in CI. A change is merged
only when every changed row covers the same bytes as before, has the ROM's SHA1, touches no
solved or claimed function, and every boundary it moves has evidence in the code:
  - a removed start is a literal-pool word loaded by the code before it in its new row;
  - an added start is called (bl) from another function, is a function pointer (addr|1) in
    the code, or is where the code starts after a removed start's pool words;
  - no changed row contains a call target or function pointer after its start.
Only PRs from trusted authors are merged: a passing PR from anyone else gets the evidence
in a comment and waits for the maintainer. Anything else is left for the maintainer, with
the reasons in a PR comment.
"""
import argparse
import bisect
import hashlib
import json
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check
import pool

BASE = check.ROM_BASE


def parse_rows(text):
    out = {}
    for line in text.splitlines()[1:]:
        if line.strip():
            addr, size, sha1 = line.strip().split(",")
            out[addr] = (int(size), sha1)
    return out


def scan(rows, rom):
    """Literal-pool loads, bl calls and function pointers in the code: (loads, calls, ptrs).

    loads/calls map a target address to the instruction addresses that load or call it."""
    loads, calls, ptrs = {}, {}, set()
    starts = [int(a, 16) for a in rows]
    lo, hi = min(starts), max(int(a, 16) + n for a, (n, _) in rows.items())
    for a, (n, _) in rows.items():
        s = int(a, 16)
        hw = struct.unpack_from("<%dH" % (n // 2), rom, s - BASE)
        i = 0
        while i < len(hw):
            at, h = s + 2 * i, hw[i]
            if h >> 11 == 0b01001:  # ldr rN, [pc, #imm8*4]
                loads.setdefault(((at + 4) & ~3) + (h & 0xFF) * 4, []).append(at)
            elif h >> 11 == 0b11110 and i + 1 < len(hw) and hw[i + 1] >> 11 == 0b11111:
                off = ((h & 0x7FF) << 12 | (hw[i + 1] & 0x7FF) << 1)
                off -= (1 << 23) if off & (1 << 22) else 0
                calls.setdefault(at + 4 + off, []).append(at)
                i += 1
            i += 1
        for j in range(s - s % 4 + (4 if s % 4 else 0), s + n - 3, 4):
            w = struct.unpack_from("<I", rom, j - BASE)[0]
            if w & 1 and lo <= w - 1 < hi:
                ptrs.add(w - 1)
    return loads, calls, ptrs


def cover(rows):
    spans = []
    for a, (n, _) in sorted(rows.items()):
        s = int(a, 16)
        if spans and spans[-1][1] == s:
            spans[-1][1] = s + n
        else:
            spans.append([s, s + n])
    return spans


def review(old, new, rom, solved, claimed):
    """old/new: ADDR -> (size, sha1). Returns (evidence notes, errors); merge only if no errors."""
    gone = {a: r for a, r in old.items() if new.get(a) != r}
    added = {a: r for a, r in new.items() if old.get(a) != r}
    if not gone and not added:
        return [], ["no rows changed"]
    errs, notes = [], []
    if cover(gone) != cover(added):
        errs.append("the changed rows do not cover the same bytes as before (removing rows is manual)")
    for a, (n, sha1) in sorted(added.items()):
        s = int(a, 16)
        if s % 4 or n <= 0 or n % 2:
            errs.append("%s: start must be 4-aligned and size even and positive" % a)
        elif hashlib.sha1(rom[s - BASE : s - BASE + n]).hexdigest() != sha1:
            errs.append("%s: SHA1 does not match the ROM" % a)
    for a in sorted(gone):
        if a in solved:
            errs.append("%s is solved; changing its row is manual" % a)
        if a in claimed:
            errs.append("%s is claimed; wait until it is solved or released" % a)
    if errs:
        return notes, errs

    loads, calls, ptrs = scan(new, rom)
    starts = sorted(int(a, 16) for a in new)

    def row_of(x):
        return starts[bisect.bisect_right(starts, x) - 1]

    removed = sorted(int(a, 16) for a in old if a not in new)
    for x in removed:
        by = [l for l in loads.get(x, []) if l < x and row_of(l) == row_of(x)]
        if by:
            notes.append("%08X is a literal-pool word loaded at %08X, so it belongs to %08X" % (x, by[0], row_of(x)))
        else:
            errs.append("%08X: no evidence it is not a function start (it is not a pool word "
                        "loaded by the code before it)" % x)
    for y in sorted(int(a, 16) for a in new if a not in old):
        prev = starts[starts.index(y) - 1] if starts.index(y) else None
        callers = [c for c in calls.get(y, []) if row_of(c) not in (y, prev)]
        pooled = [x for x in removed if x < y and all(w in loads for w in range(x, y, 4))]
        if y in ptrs:
            notes.append("%08X is a function pointer in the code" % y)
        elif callers:
            notes.append("%08X is called from %08X" % (y, callers[0]))
        elif pooled:
            notes.append("%08X is where the code starts after the pool words at %08X" % (y, pooled[0]))
        else:
            errs.append("%08X: no evidence it is a function start (not called from another "
                        "function, not a function pointer, not right after a pool)" % y)
    for a, (n, _) in sorted(added.items()):
        s = int(a, 16)
        inner = sorted(t for t in ptrs | set(calls) if s < t < s + n
                       and (t in ptrs or any(row_of(c) != s for c in calls[t])))
        if inner:
            errs.append("%s hides a function start at %08X (called or pointed to from elsewhere)" % (a, inner[0]))
    return notes, errs


def git(*args):
    return subprocess.run(["git", "-C", pool.ROOT, *args], check=True, capture_output=True, text=True).stdout


def main_state():
    """functions.csv rows, solved ADDRs and claimed ADDRs on origin/main."""
    rows = parse_rows(git("show", "origin/main:functions.csv"))
    names = git("ls-tree", "--name-only", "origin/main", "src/", "pool/claims/").split()
    solved = {p[len("src/func_"):-2] for p in names if p.startswith("src/func_")}
    claimed = {p[len("pool/claims/"):] for p in names if p.startswith("pool/claims/") and p != "pool/claims/.gitkeep"}
    return rows, solved, claimed


def review_ref(ref, rom):
    """Apply REF's functions.csv changes (relative to its merge base) to origin/main and review."""
    main_rows, solved, claimed = main_state()
    base = parse_rows(git("show", git("merge-base", "origin/main", ref).strip() + ":functions.csv"))
    head = parse_rows(git("show", ref + ":functions.csv"))
    gone = {a for a in base if head.get(a) != base[a]}
    added = {a: r for a, r in head.items() if base.get(a) != r}
    stale = [a for a in gone if main_rows.get(a) != base[a]] + [a for a in added if a in main_rows and a not in gone]
    if stale:
        return [], ["rows changed on main since this PR was made (%s): update the PR" % ", ".join(sorted(stale))]
    new = {a: r for a, r in main_rows.items() if a not in gone}
    new.update(added)
    return review(main_rows, new, rom, solved, claimed)


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, cwd=pool.ROOT).stdout


def decide(errs, author, trusted):
    """'failed', 'merge' (passed, trusted author) or 'passed' (passed, waits for the maintainer)."""
    if errs:
        return "failed"
    return "merge" if author.lower() in {t.lower() for t in trusted} else "passed"


def routine(rom, dry_run):
    git("fetch", "-q", "origin", "main")
    trusted = set(pool.load_config().get("trusted", [])) | {gh("api", "user", "-q", ".login").strip()}
    prs = json.loads(gh("pr", "list", "--state", "open", "--limit", "200",
                        "--json", "number,headRefOid,isDraft,files,comments,author"))
    for pr in prs:
        if pr["isDraft"] or [f["path"] for f in pr["files"]] != ["functions.csv"]:
            continue
        n, sha = pr["number"], pr["headRefOid"]
        git("fetch", "-q", "origin", "pull/%d/head" % n)
        notes, errs = review_ref(sha, rom)
        verdict = decide(errs, pr["author"]["login"], trusted)
        print("#%d %s by %s: %s" % (n, sha[:7], pr["author"]["login"], verdict))
        for line in notes + errs:
            print("  " + line)
        marker = "<!-- boundaries:%s -->" % sha
        if dry_run or any(marker in c["body"] for c in pr["comments"]):
            continue
        if verdict == "failed":
            body = "Boundary check: not merged automatically.\n\n" + "\n".join("- " + e for e in errs)
            body += "\n\nA maintainer will review it, or push a fix.\n" + marker
            gh("pr", "comment", str(n), "--body", body)
            continue
        body = "Boundary check passed against the ROM:\n\n" + "\n".join("- " + l for l in notes) + "\n"
        if verdict == "passed":
            body += "\nOnly PRs from trusted contributors are merged automatically; a maintainer will review it.\n"
        gh("pr", "comment", str(n), "--body", body + marker)
        if verdict == "merge":
            gh("pr", "merge", str(n), "--squash", "--match-head-commit", sha,
               "--subject", "boundaries #%d" % n, "--body", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("ref")
    r = sub.add_parser("routine")
    r.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rom = check.load_rom(pool.ROOT, pool.load_config())
    if rom is None:
        sys.exit("baserom.gba is missing: this tool runs on the maintainer's machine")
    if a.cmd == "check":
        ref = git("rev-parse", a.ref).strip()  # before the fetch, which moves FETCH_HEAD
        git("fetch", "-q", "origin", "main")
        notes, errs = review_ref(ref, rom)
        for line in notes + errs:
            print(line)
        print("OK" if not errs else "needs review")
        return 1 if errs else 0
    routine(rom, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
