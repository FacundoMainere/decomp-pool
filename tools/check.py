#!/usr/bin/env python3
"""Check one decompiled function: the same checks run locally and in CI.

  tools/check.py ADDR FILE.c   source rules, clang-format, compile with agbcc, object rules,
                               link at ADDR and compare with the SHA1 in functions.csv.
                               With baserom.gba present, a mismatch prints a side-by-side diff.
                               If FILE.cflags exists (e.g. src/func_ADDR.cflags), its one line
                               of flags replaces cflags; each flag must be in allowed_cflags.
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


def parse_cflags(text, cfg):
    """A .cflags file: one line of flags, each listed in allowed_cflags. Returns (flags, errors)."""
    if text.strip().count("\n"):
        return [], ["the .cflags file must be one line"]
    flags, allowed = text.split(), cfg.get("allowed_cflags", [])
    errs = ["%s is not in allowed_cflags in pool.toml" % f for f in flags if f not in allowed]
    if not flags:
        errs.append("the .cflags file is empty")
    if len(set(flags)) != len(flags):
        errs.append("a flag is given twice in the .cflags file")
    return flags, errs


class CheckError(Exception):
    pass


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)
    if r.returncode:
        raise CheckError("%s failed:\n%s%s" % (os.path.basename(cmd[0]), r.stdout, r.stderr))
    return r.stdout


def check_lint(path, root):
    try:
        version = run(["clang-format", "--version"])
    except FileNotFoundError:
        return ["clang-format not installed: pip install clang-format==%s" % CLANG_FORMAT]
    if CLANG_FORMAT not in version:
        return ["clang-format %s required, found %s" % (CLANG_FORMAT, version.strip())]
    r = subprocess.run(
        ["clang-format", "--dry-run", "-Werror", "--style=file:" + os.path.join(root, ".clang-format"), path],
        capture_output=True, text=True, errors="replace",
    )
    return [] if r.returncode == 0 else ["not clang-formatted (run: clang-format -i FILE)\n" + r.stderr[-2000:]]


def check_object(o, addr, cfg):
    """Only code, one global function func_ADDR, externals named by address. Returns errors."""
    errs, want = [], "func_%08X" % addr
    lines = run(["arm-none-eabi-objdump", "-h", o]).splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*\d+\s+(\S+)\s+([0-9a-f]+)\s", line)
        if m and int(m.group(2), 16) and "ALLOC" in lines[i + 1] and m.group(1) != ".text":
            errs.append("section %s is not allowed (only code: no data, strings or arrays)" % m.group(1))
    defined = False
    for line in run(["arm-none-eabi-nm", o]).splitlines():
        kind, name = line.split()[-2:]
        if kind == "U":
            if not EXTERN.fullmatch(name) and name not in cfg.get("aliases", {}):
                errs.append("undefined symbol %s: name externals func_XXXXXXXX / gUnk_XXXXXXXX" % name)
        elif name == want and kind == "T":
            defined = True
        elif name != ".gcc2_compiled.":
            errs.append("defined symbol %s is not allowed (only the function %s)" % (name, want))
    if not defined:
        errs.append("%s is not defined" % want)
    return errs


def build(path, addr, root, cfg, d, cflags=None):
    """Compile path (with cflags, or pool.toml's for addr) and link it at addr. Returns the .text
    bytes. Raises CheckError."""
    pp = run(["cpp", "-nostdinc", "-undef", "-I", os.path.join(root, "include"),
              "-I", os.path.join(root, "tools", "agbcc", "include"), path])
    s, o, so, elf, b = (os.path.join(d, x) for x in ("f.s", "f.o", "syms.o", "f.elf", "f.bin"))
    cflags = cflags or cfg.get("function_cflags", {}).get("%08X" % addr, cfg["cflags"])
    run([os.path.join(root, cfg["compiler"])] + cflags + ["-o", s], input=pp)
    with open(s, "a") as f:
        f.write("\t.align 2, 0\n")
    run(["arm-none-eabi-as", "-mcpu=arm7tdmi", "-o", o, s])
    errs = check_object(o, addr, cfg)
    if errs:
        raise CheckError("\n".join(errs))
    # Externals resolve to the address in their name; functions need .thumb_set so ld emits a
    # direct bl instead of an interworking veneer.
    stub = ["\t.syntax unified"]
    for line in run(["arm-none-eabi-nm", "-u", o]).splitlines():
        name = line.split()[-1]
        m = EXTERN.fullmatch(name)
        value, func = (int(m[2], 16), m[1] == "func") if m else (cfg["aliases"][name], True)
        stub += ["\t.global %s" % name, "\t.%s %s, %#x" % ("thumb_set" if func else "set", name, value)]
    with open(so + ".s", "w") as f:
        f.write("\n".join(stub) + "\n")
    run(["arm-none-eabi-as", "-mcpu=arm7tdmi", "-o", so, so + ".s"])
    run(["arm-none-eabi-ld", "-Ttext=%#x" % addr, "-e", "%#x" % addr, "-o", elf, o, so])
    run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".text", elf, b])
    with open(b, "rb") as f:
        return f.read()


def compare(got, size, sha1):
    """got is the function plus at most 3 zero bytes of alignment padding."""
    pad = got[size:]
    return len(got) >= size and len(pad) < 4 and not any(pad) and hashlib.sha1(got[:size]).hexdigest() == sha1


def disasm(code, addr, d):
    p = os.path.join(d, "dis.bin")
    with open(p, "wb") as f:
        f.write(code)
    out = run(["arm-none-eabi-objdump", "-D", "-b", "binary", "-marmv4t", "-M", "force-thumb",
               "--adjust-vma=%#x" % addr, p])
    return [l.split(":", 1)[1].strip() for l in out.splitlines() if re.match(r"\s+[0-9a-f]+:", l)]


def side_by_side(want, got, addr, d):
    a, b = disasm(want, addr, d), disasm(got, addr, d)
    rows = ["  %-44s | %s" % ("original", "yours")]
    for i in range(max(len(a), len(b))):
        x, y = (a[i] if i < len(a) else ""), (b[i] if i < len(b) else "")
        rows.append("%s %-44s | %s" % (" " if x == y else "*", x[:44], y[:44]))
    return "\n".join(rows)


def check_solve(root, addr, text, cfg, rom=None, cflags=None):
    """All solve checks for func_ADDR with source text and the text of its .cflags file, if any.
    Returns a list of errors (empty = MATCH)."""
    funcs = pool.load_functions(root)
    if addr not in funcs:
        return ["%s is not in functions.csv" % addr]
    errs = check_source(text, root, cfg)
    flags = None
    if cflags is not None:
        flags, e = parse_cflags(cflags, cfg)
        errs += e
    if errs:
        return errs
    size, sha1 = funcs[addr]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "func_%s.c" % addr)
        with open(path, "w") as f:
            f.write(text)
        errs = check_lint(path, root)
        try:
            got = build(path, int(addr, 16), root, cfg, d, flags)
        except CheckError as e:
            return errs + [str(e)]
        if compare(got, size, sha1):
            return errs
        msg = "NO MATCH: got %d bytes, want %d" % (len(got), size)
        if rom:
            off = int(addr, 16) - ROM_BASE
            msg += "\n" + side_by_side(rom[off:off + size], got, int(addr, 16), d)
        return errs + [msg]


def load_rom(root, cfg):
    p = os.path.join(root, "baserom.gba")
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        rom = f.read()
    if cfg["rom_sha1"] and hashlib.sha1(rom).hexdigest() != cfg["rom_sha1"]:
        sys.exit("baserom.gba is not the right ROM (sha1 must be %s)" % cfg["rom_sha1"])
    return rom


def main(argv):
    root, cfg = pool.ROOT, pool.load_config()
    if len(argv) != 2:
        sys.exit(__doc__)
    rom = load_rom(root, cfg)
    if argv[0] == "--asm":
        addr = argv[1].upper()
        if rom is None:
            sys.exit("put your own ROM at baserom.gba")
        size = pool.load_functions(root)[addr][0]
        off = int(addr, 16) - ROM_BASE
        with tempfile.TemporaryDirectory() as d:
            print("\n".join(disasm(rom[off:off + size], int(addr, 16), d)))
        return 0
    addr = argv[0].upper()
    cflags = None
    if os.path.exists(os.path.splitext(argv[1])[0] + ".cflags"):
        with open(os.path.splitext(argv[1])[0] + ".cflags") as f:
            cflags = f.read()
    with open(argv[1]) as f:
        errs = check_solve(root, addr, f.read(), cfg, rom, cflags)
    print("\n".join(errs) if errs else "MATCH %s" % addr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
