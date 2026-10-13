#!/usr/bin/env python3
"""Pool state and PR validation (stdlib only).

  tools/pool.py free [N]        list free functions (ADDR SIZE), smallest first
  tools/pool.py validate --author USER --created ISO --ref GITREF [--state-only]
                                validate the PR whose head is GITREF against the checkout (main).
                                Prints {"kind", "ok", "reason"} as JSON; exit 1 unless ok.
  tools/pool.py expire          delete expired claims and add strikes (run by the cron)

State is only files: functions.csv, pool/claims/ADDR ("<user> <timestamp>"),
pool/strikes/USER ("<count>"), src/func_ADDR.c. See docs/specs for the rules.
"""
import argparse
import datetime as dt
import json
import os
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
