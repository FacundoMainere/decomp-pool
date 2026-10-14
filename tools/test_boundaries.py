import hashlib
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import boundaries

BASE = 0x08000000


def bl(at, target):
    off = target - (at + 4)
    return struct.pack("<HH", 0xF000 | ((off >> 12) & 0x7FF), 0xF800 | ((off >> 1) & 0x7FF))


# 0x00 A: push {lr}; ldr r0,[pc,#4] (loads the word at 0x08); pop {pc}; pad; pool word
# 0x0C C: push {lr}; nop; pop {pc}; pad; then 0x14 E: push {lr}; pop {pc}
# 0x18 D: push {lr}; bl C; bl E; pop {pc}
ROM = bytearray(0x28)
ROM[0x00:0x08] = struct.pack("<HHHH", 0xB500, 0x4801, 0xBD00, 0x0000)
ROM[0x08:0x0C] = struct.pack("<I", 0x12345678)
ROM[0x0C:0x14] = struct.pack("<HHHH", 0xB500, 0x46C0, 0xBD00, 0x0000)
ROM[0x14:0x18] = struct.pack("<HH", 0xB500, 0xBD00)
ROM[0x18:0x1A] = struct.pack("<H", 0xB500)
ROM[0x1A:0x1E] = bl(BASE + 0x1A, BASE + 0x0C)
ROM[0x1E:0x22] = bl(BASE + 0x1E, BASE + 0x14)
ROM[0x22:0x24] = struct.pack("<H", 0xBD00)
ROM = bytes(ROM)


def rows(*spans):
    """(start offset, size) -> functions.csv rows with the right SHA1s."""
    return {
        "%08X" % (BASE + s): (n, hashlib.sha1(ROM[s : s + n]).hexdigest()) for s, n in spans
    }


GOOD = rows((0x00, 0x0C), (0x0C, 0x08), (0x14, 0x04), (0x18, 0x10))


class ReviewTest(unittest.TestCase):
    def review(self, old, new, solved=(), claimed=()):
        return boundaries.review(old, new, ROM, set(solved), set(claimed))

    def test_pool_word_row_merged_into_its_function(self):
        old = rows((0x00, 0x08), (0x08, 0x04), (0x0C, 0x08), (0x14, 0x04), (0x18, 0x10))
        notes, errs = self.review(old, GOOD)
        self.assertEqual(errs, [])
        self.assertTrue(any("08000008" in n for n in notes))

    def test_row_starting_on_pool_shifted_to_the_real_start(self):
        old = rows((0x00, 0x08), (0x08, 0x0C), (0x14, 0x04), (0x18, 0x10))
        self.assertEqual(self.review(old, GOOD)[1], [])

    def test_merging_a_real_function_is_refused(self):
        new = rows((0x00, 0x14), (0x14, 0x04), (0x18, 0x10))
        errs = self.review(GOOD, new)[1]
        self.assertTrue(any("0800000C" in e for e in errs), errs)

    def test_split_needs_entry_evidence(self):
        old = rows((0x00, 0x0C), (0x0C, 0x0C), (0x18, 0x10))
        self.assertEqual(self.review(old, GOOD)[1], [])  # 0x14 is called from D
        new = rows((0x00, 0x0C), (0x0C, 0x04), (0x10, 0x08), (0x18, 0x10))
        errs = self.review(GOOD, new)[1]
        self.assertTrue(any("08000010" in e for e in errs), errs)

    def test_wrong_sha1_coverage_and_touched_rows(self):
        old = rows((0x00, 0x08), (0x08, 0x04), (0x0C, 0x08), (0x14, 0x04), (0x18, 0x10))
        bad = dict(GOOD)
        bad["08000000"] = (0x0C, "0" * 40)
        self.assertTrue(any("SHA1" in e for e in self.review(old, bad)[1]))
        short = {a: r for a, r in GOOD.items() if a != "08000018"}
        self.assertTrue(any("cover" in e for e in self.review(GOOD, short)[1]))
        self.assertTrue(any("solved" in e for e in self.review(old, GOOD, solved=["08000008"])[1]))
        self.assertTrue(any("claimed" in e for e in self.review(old, GOOD, claimed=["08000000"])[1]))

    def test_no_change(self):
        self.assertEqual(self.review(GOOD, GOOD), ([], ["no rows changed"]))


if __name__ == "__main__":
    unittest.main()
