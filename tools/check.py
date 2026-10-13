#!/usr/bin/env python3
"""Check one decompiled function: the same checks run locally and in CI.

  tools/check.py ADDR FILE.c   source rules, clang-format, compile with agbcc, object rules,
                               link at ADDR and compare with the SHA1 in functions.csv.
                               With baserom.gba present, a mismatch prints a side-by-side diff.
  tools/check.py --asm ADDR    disassemble the original function from baserom.gba
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool

ROM_BASE = 0x08000000  # ponytail: GBA only; make it a pool.toml key when another platform comes
CLANG_FORMAT = "18.1.8"
BANNED = re.compile(r"\b(asm|__asm|__asm__|__attribute__|__attribute|_Pragma)\b")
TOKENS = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//|/\*')
INCLUDE = re.compile(r'\s*#\s*include\s*"([^"]+)"\s*')
EXTERN = re.compile(r"(func|gUnk)_([0-9A-F]{8})")


def check_source(text, root, cfg):
    """Rules on the C text. Returns a list of errors."""
    if len(text.encode()) > cfg["max_source_bytes"]:
        return ["source is over %d bytes" % cfg["max_source_bytes"]]
    errs = []
    text = re.sub(r"\\[ \t\r]*\n", "", text)  # line splices first, as the preprocessor does

    def strip(m):
        if m.group() in ("//", "/*"):
            errs.append("comments are not allowed (merged code is read by other agents)")
            return " "
        return '""'

    code = TOKENS.sub(strip, text)
    if "%:" in code or "??" in code:
        errs.append("digraphs and trigraphs (%:, ??) are not allowed")
    errs += ["%s is not allowed" % w for w in sorted(set(BANNED.findall(code)))]
    inc = os.path.realpath(os.path.join(root, "include"))
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            m = INCLUDE.fullmatch(line)
            p = m and os.path.realpath(os.path.join(inc, m.group(1)))
            if not m or not p.startswith(inc + os.sep) or not os.path.isfile(p):
                errs.append('only #include "X" of a file in include/ is allowed: %s' % line.strip())
    return list(dict.fromkeys(errs))
