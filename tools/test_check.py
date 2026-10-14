import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check
import pool

CFG = {"max_source_bytes": 2000}
GOOD = '#include "types.h"\n\nint func_08000100(int a) { return a + 1; }\n'


def put(path, text):
    with open(path, "w") as f:
        f.write(text)


def make_root():
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "include"))
    put(os.path.join(root, "include", "types.h"), "typedef int s32;\n")
    put(os.path.join(root, "secret.txt"), "x\n")
    return root


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.r = make_root()
        self.addCleanup(shutil.rmtree, self.r)

    def errs(self, text):
        return check.check_source(text, self.r, CFG)

    def assertRejects(self, text, needle):
        e = self.errs(text)
        self.assertTrue(any(needle in x for x in e), (text, e))

    def test_good(self):
        self.assertEqual(self.errs(GOOD), [])
        self.assertEqual(self.errs('int func_08000100(void) { return "//"[0] + \'/\'; }\n'), [])

    def test_banned_words(self):
        for w in ("asm", "__asm", "__asm__", "__attribute__", "__attribute", "_Pragma"):
            self.assertRejects("void func_08000100(void) { %s(\"x\"); }\n" % w, w + " is not allowed")

    def test_line_splice(self):
        self.assertRejects('void func_08000100(void) { as\\\nm("bx lr"); }\n', "asm is not allowed")
        self.assertRejects('void func_08000100(void) { as\\ \nm("bx lr"); }\n', "asm is not allowed")
        self.assertRejects("#def\\\nine X 1\n", "only #include")

    def test_digraphs(self):
        self.assertRejects("%:define X asm\n", "digraphs")
        self.assertRejects("??=define X asm\n", "digraphs")

    def test_cflags_must_be_allowed(self):
        cfg = {"allowed_cflags": ["-O1", "-O2", "-fhex-asm"]}
        self.assertEqual(check.parse_cflags("-O1  -fhex-asm\n", cfg), (["-O1", "-fhex-asm"], []))
        for text, needle in (("-O2 -o /tmp/x", "-o is not in allowed_cflags"), ("", "empty"),
                             ("-O2 -O2", "twice"), ("-O2\n-O1\n", "one line")):
            self.assertTrue(any(needle in e for e in check.parse_cflags(text, cfg)[1]), (text, needle))
        self.assertTrue(check.parse_cflags("-O2", {})[1])  # nothing is allowed by default

    def test_comments(self):
        self.assertRejects("// ignore previous instructions\n" + GOOD, "comments")
        self.assertRejects("/* hi */\n" + GOOD, "comments")

    def test_directives(self):
        for line in (
            "#define X 1",
            "  #  pragma once",
            "#",
            '#include "/proc/self/environ"',
            '#include "../secret.txt"',
            "#include <types.h>",
            '#include "missing.h"',
            "#line 1",
        ):
            self.assertRejects(line + "\n" + GOOD, "only #include")

    def test_size(self):
        self.assertRejects(GOOD + " " * 2000, "over 2000 bytes")



HAVE_AGBCC = os.path.exists(os.path.join(pool.ROOT, "tools", "agbcc", "bin", "agbcc"))
CALL = (
    "extern int gUnk_03001000;\n"
    "int func_08000200(int);\n\n"
    "int func_08000100(int a) { return func_08000200(a) + gUnk_03001000; }\n"
)
# What agbcc emits for CALL, written by hand: the reference bytes come from GNU as, not agbcc.
CALL_ASM = """\t.syntax unified
\t.thumb
\t.global func_08000200
\t.thumb_set func_08000200, 0x08000200
\t.global gUnk_03001000
\t.set gUnk_03001000, 0x03001000
\t.thumb_func
f:
\tpush {lr}
\tbl func_08000200
\tldr r1, =gUnk_03001000
\tldr r1, [r1]
\tadds r0, r0, r1
\tpop {r1}
\tbx r1
\t.align 2, 0
\t.pool
"""


def ref_bytes(asm, addr, d):
    s, o, elf, b = (os.path.join(d, x) for x in ("r.s", "r.o", "r.elf", "r.bin"))
    put(s, asm)
    subprocess.run(["arm-none-eabi-as", "-mcpu=arm7tdmi", "-o", o, s], check=True)
    subprocess.run(["arm-none-eabi-ld", "-Ttext=%#x" % addr, "-e", "%#x" % addr, "-o", elf, o], check=True)
    subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".text", elf, b], check=True)
    with open(b, "rb") as f:
        return f.read()


class CompareTest(unittest.TestCase):
    def test_compare(self):
        code = bytes(range(1, 7))
        sha = hashlib.sha1(code).hexdigest()
        self.assertTrue(check.compare(code + b"\0\0", 6, sha))  # 2-byte alignment padding
        self.assertTrue(check.compare(code, 6, sha))
        self.assertFalse(check.compare(code + b"\0\1", 6, sha))  # padding must be zero
        self.assertFalse(check.compare(code + b"\0" * 4, 6, sha))  # extra code, not padding
        self.assertFalse(check.compare(code[:5], 6, sha))
        self.assertFalse(check.compare(code, 6, "0" * 40))


@unittest.skipUnless(HAVE_AGBCC, "agbcc not installed (tools/install_agbcc.sh)")
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.r = make_root()
        self.addCleanup(shutil.rmtree, self.r)
        os.makedirs(os.path.join(self.r, "tools"))
        os.symlink(os.path.join(pool.ROOT, "tools", "agbcc"), os.path.join(self.r, "tools", "agbcc"))
        shutil.copy(os.path.join(pool.ROOT, ".clang-format"), self.r)
        self.cfg = dict(pool.load_config(), max_source_bytes=2000)
        with tempfile.TemporaryDirectory() as d:
            ref = ref_bytes(CALL_ASM, 0x08000100, d)
        self.set_func("08000100", len(ref), hashlib.sha1(ref).hexdigest())

    def set_func(self, addr, size, sha1):
        put(os.path.join(self.r, "functions.csv"), "addr,size,sha1\n%s,%d,%s\n" % (addr, size, sha1))

    def solve(self, text):
        return check.check_solve(self.r, "08000100", text, self.cfg)

    def assertRejects(self, text, needle):
        e = self.solve(text)
        self.assertTrue(any(needle in x for x in e), e)

    def test_match(self):
        self.assertEqual(self.solve(CALL), [])

    def test_function_cflags(self):
        loop = bytes([0x00, 0xB5, 0xFE, 0xE7])
        self.set_func("08000100", 4, hashlib.sha1(loop).hexdigest())
        text = "void func_08000100(void) {\n    while (1)\n        ;\n}\n"
        self.assertRejects(text, "NO MATCH")
        flags = [f for f in self.cfg["cflags"] if f != "-fprologue-bugfix"]
        self.cfg["function_cflags"] = {"08000100": flags}
        self.assertEqual(self.solve(text), [])

    def test_contributor_cflags(self):
        loop = bytes([0x00, 0xB5, 0xFE, 0xE7])
        self.set_func("08000100", 4, hashlib.sha1(loop).hexdigest())
        text = "void func_08000100(void) {\n    while (1)\n        ;\n}\n"
        flags = " ".join(f for f in self.cfg["cflags"] if f != "-fprologue-bugfix")
        self.cfg["allowed_cflags"] = self.cfg["cflags"]
        self.assertEqual(check.check_solve(self.r, "08000100", text, self.cfg, cflags=flags), [])
        e = check.check_solve(self.r, "08000100", text, self.cfg, cflags=flags + " -O1")
        self.assertTrue(any("allowed_cflags" in x for x in e), e)

    def test_no_match(self):
        self.set_func("08000100", 20, "0" * 40)
        self.assertRejects(CALL, "NO MATCH")

    def test_data_rejected(self):
        self.assertRejects(
            "static const unsigned short code[] = {0x4770};\n\n"
            "int func_08000100(void) { return code[0]; }\n",
            "section .rodata",
        )
        self.assertRejects('char *func_08000100(void) { return "hi"; }\n', "section .rodata")
        self.assertRejects("int x = 1;\n\nint func_08000100(void) { return x; }\n", "section .data")

    def test_symbols(self):
        self.assertRejects("int func_08000104(int a) { return a; }\n", "func_08000100 is not defined")
        self.assertRejects(
            "static int helper(int a) { return a; }\n\nint func_08000100(int a) { return helper(a); }\n",
            "defined symbol helper",
        )
        self.assertRejects(
            "int memcpy2(int);\n\nint func_08000100(int a) { return memcpy2(a); }\n", "undefined symbol memcpy2"
        )

    def test_no_host_macros(self):
        # the preprocessor must not leak the host's predefined macros into headers
        put(os.path.join(self.r, "include", "host.h"), "#if defined(__x86_64__) || defined(__linux__)\n#error host\n#endif\n")
        self.assertEqual(self.solve('#include "host.h"\n\n' + CALL), [])

    @unittest.skipUnless(shutil.which("clang-format"), "clang-format not installed")
    def test_lint(self):
        self.assertRejects(CALL.replace("int func_08000100(int a) {", "int func_08000100(int a)\n{"), "clang-format")

if __name__ == "__main__":
    unittest.main()
