#!/usr/bin/env python3
"""Pool state and PR validation (stdlib only).

  tools/pool.py free [N] [--slot K/N] [--random]
                                list free functions (ADDR SIZE), smallest first; --slot keeps
                                agent K of N's share, --random shuffles the 50 smallest
  tools/pool.py validate --author USER --created ISO --ref GITREF [--state-only]
                                validate the PR whose head is GITREF against the checkout (main).
                                Prints {"kind", "ok", "reason"} as JSON; exit 1 unless ok.
  tools/pool.py expire          delete expired claims and add strikes (run by the cron)
  tools/pool.py progress [--write]  solved bytes and functions, active claims; --write updates
                                docs/PROGRESS.md (run by the cron)

State is only files: functions.csv, pool/claims/ADDR ("<user> <timestamp>"),
pool/strikes/USER ("<count>"), src/func_ADDR.c. See docs/specs for the rules.
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDR = r"[0-9A-F]{8}"
TS = "%Y-%m-%dT%H:%M:%SZ"
UTC = dt.timezone.utc


def load_config(root=ROOT):
    with open(os.path.join(root, "pool.toml"), "rb") as f:
        return tomllib.load(f)


def load_functions(root=ROOT):
    """ADDR -> (size, sha1)."""
    out = {}
    with open(os.path.join(root, "functions.csv")) as f:
        next(f)
        for line in f:
            if line.strip():
                addr, size, sha1 = line.strip().split(",")
                out[addr] = (int(size), sha1)
    return out


def parse_ts(s):
    return dt.datetime.strptime(s, TS).replace(tzinfo=UTC)


def parse_claim(text):
    """'<user> <timestamp>' -> (user, datetime). Raises ValueError."""
    user, stamp = text.strip().split(" ")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", user):
        raise ValueError("bad user %r" % user)
    return user, parse_ts(stamp)


def read_claims(root=ROOT):
    """ADDR -> (user, claimed_at) for every claim file, active or expired."""
    d = os.path.join(root, "pool", "claims")
    out = {}
    for name in sorted(os.listdir(d)):
        if re.fullmatch(ADDR, name):
            with open(os.path.join(d, name)) as f:
                out[name] = parse_claim(f.read())
    return out


def read_strikes(root=ROOT):
    """lowercase user -> strike count."""
    d = os.path.join(root, "pool", "strikes")
    out = {}
    for name in os.listdir(d):
        if not name.startswith("."):
            with open(os.path.join(d, name)) as f:
                out[name.lower()] = int(f.read())
    return out


def active(claimed_at, now, cfg):
    return now < claimed_at + dt.timedelta(hours=cfg["claim_hours"])


def solved(root, addr):
    return os.path.exists(os.path.join(root, "src", "func_%s.c" % addr))


def free(root, now, cfg):
    """Free ADDRs, smallest function first."""
    funcs, claims = load_functions(root), read_claims(root)
    return sorted(
        (a for a in funcs if not solved(root, a) and not (a in claims and active(claims[a][1], now, cfg))),
        key=lambda a: (funcs[a][0], a),
    )


def pick(addrs, slot=None, rand=None, window=50):
    """Order free ADDRs for one agent.

    slot=(K, N) keeps the ADDRs with (addr / 4) mod N == K-1: a fixed share that does not move
    when other functions are claimed, so N agents of one person never pick the same function.
    rand shuffles the `window` smallest, so agents of different people rarely do."""
    if slot:
        k, n = slot
        addrs = [a for a in addrs if int(a, 16) // 4 % n == k - 1]
    if rand:
        head = addrs[:window]
        rand.shuffle(head)
        addrs = head + addrs[window:]
    return addrs


CLAIM_RE = re.compile(r"pool/claims/(%s)" % ADDR)
SOLVE_RE = re.compile(r"src/func_(%s)\.c" % ADDR)


def progress(root, now, cfg):
    funcs, claims = load_functions(root), read_claims(root)
    done = [a for a in funcs if solved(root, a)]
    return {
        "functions": len(funcs),
        "bytes": sum(size for size, _ in funcs.values()),
        "solved": len(done),
        "solved_bytes": sum(funcs[a][0] for a in done),
        "claimed": sum(1 for a, (_, t) in claims.items() if a in funcs and a not in done and active(t, now, cfg)),
    }


def progress_line(p):
    pct = 100.0 * p["solved_bytes"] / p["bytes"] if p["bytes"] else 0.0
    return "%d / %d bytes solved (%.2f%%), %d / %d functions" % (
        p["solved_bytes"], p["bytes"], pct, p["solved"], p["functions"])


def write_progress(root, now, cfg):
    """Rewrite the block between the progress markers in docs/PROGRESS.md. True if it changed."""
    path = os.path.join(root, "docs", "PROGRESS.md")
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        return False
    m = re.search(r"(<!-- progress:start -->\n)(.*?)(<!-- progress:end -->)", text, re.S)
    if not m:
        return False
    new = text[: m.end(1)] + progress_line(progress(root, now, cfg)) + "\n" + text[m.start(3):]
    if new == text:
        return False
    with open(path, "w") as f:
        f.write(new)
    return True


def classify(changes):
    """[(status, path)] -> 'claim' | 'release' | 'solve' | 'manual'."""
    claims = [(s, CLAIM_RE.fullmatch(p)[1]) for s, p in changes if CLAIM_RE.fullmatch(p)]
    solves = [(s, SOLVE_RE.fullmatch(p)[1]) for s, p in changes if SOLVE_RE.fullmatch(p)]
    if not changes or len(claims) + len(solves) != len(changes):
        return "manual"
    if not solves:
        if all(s in "AM" for s, _ in claims):
            return "claim"
        if all(s == "D" for s, _ in claims):
            return "release"
        return "manual"
    if len(solves) == 1 and solves[0][0] == "A" and claims == [("D", solves[0][1])]:
        return "solve"
    return "manual"


def validate(root, changes, contents, author, created, now, cfg, state_only=False):
    """Validate a PR against the state in root. Returns (kind, errors)."""
    kind = classify(changes)
    if kind == "manual":
        return kind, ["not a claim, release or solve PR; a maintainer will review it"]
    funcs, claims, strikes = load_functions(root), read_claims(root), read_strikes(root)
    me = author.lower()
    mine = lambda a: a in claims and claims[a][0].lower() == me
    addrs = [CLAIM_RE.fullmatch(p)[1] for _, p in changes if CLAIM_RE.fullmatch(p)]
    errs = []
    if kind == "claim":
        if strikes.get(me, 0) >= cfg["max_strikes"]:
            errs.append("%s is banned (%d expired claims)" % (author, strikes[me]))
        held = sum(1 for a, (u, t) in claims.items() if u.lower() == me and active(t, now, cfg) and not solved(root, a))
        if held + len(addrs) > cfg["max_claims"]:
            errs.append("you hold %d claims; %d more exceeds max_claims = %d" % (held, len(addrs), cfg["max_claims"]))
        skew = dt.timedelta(minutes=cfg["timestamp_skew_min"])
        for a in addrs:
            p = "pool/claims/" + a
            try:
                user, t = parse_claim(contents[p])
            except ValueError:
                errs.append("%s: content must be '<user> <YYYY-MM-DDTHH:MM:SSZ>'" % p)
                continue
            if user.lower() != me:
                errs.append("%s: user %s must be the PR author %s" % (p, user, author))
            if t > now:
                errs.append("%s: timestamp is in the future" % p)
            if abs(t - created) > skew:
                errs.append("%s: timestamp must be within %d minutes of the PR creation" % (p, cfg["timestamp_skew_min"]))
            if a not in funcs:
                errs.append("%s is not in functions.csv" % a)
            elif solved(root, a):
                errs.append("%s is already solved" % a)
            elif a in claims and active(claims[a][1], now, cfg):
                errs.append("%s is claimed by %s" % (a, claims[a][0]))
            elif mine(a):
                errs.append("%s: your claim expired; wait for the expiry run before claiming it again" % a)
    elif kind == "release":
        for a in addrs:
            if not mine(a):
                errs.append("%s: claim is not yours" % a)
            elif not active(claims[a][1], now, cfg):
                errs.append("%s: your claim expired; wait for the expiry run" % a)
    else:
        a = addrs[0]
        if not mine(a):
            errs.append("%s: claim is not yours (claim it first)" % a)
        elif not active(claims[a][1], now, cfg):
            errs.append("%s: your claim expired" % a)
        if solved(root, a):
            errs.append("%s is already solved" % a)
        if not errs and not state_only:
            import check

            errs += check.check_solve(root, a, contents["src/func_%s.c" % a], cfg)
    return kind, errs


def expire(root, now, cfg):
    """Delete expired (and leftover solved) claims; one strike per expired claim. Returns [(addr, user)]."""
    gone = []
    for a, (user, t) in read_claims(root).items():
        if solved(root, a) or not active(t, now, cfg):
            os.remove(os.path.join(root, "pool", "claims", a))
            if not solved(root, a):
                gone.append((a, user))
                p = os.path.join(root, "pool", "strikes", user.lower())
                n = read_strikes(root).get(user.lower(), 0)
                with open(p, "w") as f:
                    f.write("%d\n" % (n + 1))
    return gone


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, errors="replace", check=True).stdout


def pr_changes(ref, root=ROOT):
    """The PR's own changes (merge base of HEAD and ref -> ref) and the new file contents.
    Reads git objects only; nothing from the PR is checked out or executed."""
    out = git(root, "diff", "--name-status", "--no-renames", "HEAD..." + ref)
    changes = [tuple(line.split("\t", 1)) for line in out.splitlines()]
    contents = {p: git(root, "show", "%s:%s" % (ref, p)) for s, p in changes if s != "D"}
    return changes, contents


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("free")
    f.add_argument("n", nargs="?", type=int, default=20)
    f.add_argument("--slot", help="K/N: only agent K of N's share of the functions")
    f.add_argument("--random", action="store_true", help="shuffle the 50 smallest")
    v = sub.add_parser("validate")
    v.add_argument("--author", required=True)
    v.add_argument("--created", required=True, help="PR creation time, YYYY-MM-DDTHH:MM:SSZ")
    v.add_argument("--ref", required=True, help="git ref of the PR head")
    v.add_argument("--state-only", action="store_true", help="skip compiling (claims, the merge re-check)")
    sub.add_parser("expire")
    pg = sub.add_parser("progress")
    pg.add_argument("--write", action="store_true", help="update the block in docs/PROGRESS.md")
    a = ap.parse_args()
    cfg, now = load_config(), dt.datetime.now(UTC)
    if a.cmd == "free":
        funcs = load_functions()
        slot = tuple(map(int, a.slot.split("/"))) if a.slot else None
        if slot and not 1 <= slot[0] <= slot[1]:
            ap.error("--slot K/N needs 1 <= K <= N")
        for addr in pick(free(ROOT, now, cfg), slot, random.Random() if a.random else None)[: a.n]:
            print(addr, funcs[addr][0])
    elif a.cmd == "validate":
        changes, contents = pr_changes(a.ref)
        kind, errs = validate(ROOT, changes, contents, a.author, parse_ts(a.created), now, cfg, a.state_only)
        print(json.dumps({"kind": kind, "ok": not errs, "reason": "\n".join(errs)[:3000]}))
        return 1 if errs else 0
    elif a.cmd == "progress":
        if a.write:
            write_progress(ROOT, now, cfg)
        p = progress(ROOT, now, cfg)
        print("%s, %d claimed" % (progress_line(p), p["claimed"]))
    else:
        for addr, user in expire(ROOT, now, cfg):
            print("expired %s (%s)" % (addr, user))
    return 0


if __name__ == "__main__":
    sys.exit(main())
