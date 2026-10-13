import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setup

B = 0x08000000


class SetupTest(unittest.TestCase):
    def test_rows(self):
        rom = bytes(range(256)) * 4
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("start,end,thumb,name\n")
            f.write("%08x,%08x,1,a\n" % (B + 0x10, B + 0x13))  # size runs to next start (0x20), not end
            f.write("%08x,%08x,0,arm\n" % (B + 0x20, B + 0x2F))  # ARM: skipped but bounds the previous
            f.write("%08x,%08x,1,odd\n" % (B + 0x32, B + 0x3F))  # not 4-aligned: skipped
            f.write("%08x,%08x,1,b\n" % (B + 0x40, B + 0x47))
            f.write("%08x,%08x,1,b\n" % (B + 0x40, B + 0x47))  # duplicate row
            f.write("%08x,%08x,1,last\n" % (B + 0x50, B + 0x55))  # last in the code region: runs to code_end
            f.write("%08x,%08x,1,data\n" % (B + 0x200, B + 0x203))  # past code_end
        self.addCleanup(os.remove, f.name)
        rows = setup.rows(rom, f.name, B + 0x100)
        sha = lambda s, e: hashlib.sha1(rom[s:e]).hexdigest()
        self.assertEqual(rows, [
            ("08000010", 0x10, sha(0x10, 0x20)),
            ("08000040", 0x10, sha(0x40, 0x50)),
            ("08000050", 0xB0, sha(0x50, 0x100)),
        ])
        # with no code_end limit the very last function runs to its Ghidra end + 1
        self.assertEqual(setup.rows(rom, f.name, B + len(rom))[-1], ("08000200", 4, sha(0x200, 0x204)))


if __name__ == "__main__":
    unittest.main()
