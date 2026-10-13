#!/usr/bin/env python3
"""Build functions.csv from your ROM and a Ghidra function export (see docs/NEW_PROJECT.md).

  tools/setup.py baserom.gba funcs.csv [--code-end 0x08056536]

Each function runs to the next function's start, so literal pools belong to the function
before them. Keeps thumb functions with 4-aligned starts below --code-end. Writes
functions.csv (addr,size,sha1: no ROM bytes) and sets rom_sha1 in pool.toml.
"""
import argparse
import csv
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool
from check import ROM_BASE


def rows(rom, export, code_end):
    with open(export) as f:
        funcs = sorted({int(r["start"], 16): (int(r["end"], 16), r["thumb"] == "1") for r in csv.DictReader(f)}.items())
    out = []
    for i, (start, (end, thumb)) in enumerate(funcs):
        if start >= code_end:
            break
        stop = min(funcs[i + 1][0] if i + 1 < len(funcs) else end + 1, code_end)
        if thumb and start % 4 == 0 and stop > start:
            code = rom[start - ROM_BASE:stop - ROM_BASE]
            out.append(("%08X" % start, len(code), hashlib.sha1(code).hexdigest()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("export")
    ap.add_argument("--code-end", type=lambda s: int(s, 16), help="end of the code region (hex)")
    a = ap.parse_args()
    with open(a.rom, "rb") as f:
        rom = f.read()
    out = rows(rom, a.export, a.code_end or ROM_BASE + len(rom))
    with open(os.path.join(pool.ROOT, "functions.csv"), "w") as f:
        f.write("addr,size,sha1\n" + "".join("%s,%d,%s\n" % r for r in out))
    p = os.path.join(pool.ROOT, "pool.toml")
    with open(p) as f:
        toml = f.read()
    toml = re.sub(r'^rom_sha1 = ".*?"', 'rom_sha1 = "%s"' % hashlib.sha1(rom).hexdigest(), toml, count=1, flags=re.M)
    with open(p, "w") as f:
        f.write(toml)
    print("%d functions, %d bytes -> functions.csv" % (len(out), sum(r[1] for r in out)))


if __name__ == "__main__":
    main()
