import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool

NOW = dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc)
CFG = {"max_claims": 2, "claim_hours": 48, "max_strikes": 3, "timestamp_skew_min": 15}


def ts(hours_ago=0.0):
    return (NOW - dt.timedelta(hours=hours_ago)).strftime(pool.TS)


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


class PoolTest(unittest.TestCase):
    def root(self, funcs=("08000100", "08000200", "08000300"), claims=None, strikes=None, solved=()):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        sizes = {a: 4 * (len(funcs) - i) for i, a in enumerate(funcs)}  # first one is the biggest
        write(root, "functions.csv", "addr,size,sha1\n" + "".join("%s,%d,%s\n" % (a, sizes[a], "0" * 40) for a in funcs))
        write(root, "pool/claims/.gitkeep", "")
        write(root, "pool/strikes/.gitkeep", "")
        for a, text in (claims or {}).items():
            write(root, "pool/claims/" + a, text + "\n")
        for u, n in (strikes or {}).items():
            write(root, "pool/strikes/" + u, "%d\n" % n)
        for a in solved:
            write(root, "src/func_%s.c" % a, "")
        return root


class StateTest(PoolTest):
    def test_parse_claim(self):
        self.assertEqual(pool.parse_claim("juan %s\n" % ts()), ("juan", NOW))
        for bad in ("juan", "juan  " + ts(), "juan 2026-10-13", "ju an " + ts(), "juan %s x" % ts()):
            with self.assertRaises(ValueError, msg=bad):
                pool.parse_claim(bad)

    def test_read_state(self):
        root = self.root(claims={"08000100": "juan " + ts(1)}, strikes={"ema": 2})
        self.assertEqual(pool.read_claims(root), {"08000100": ("juan", NOW - dt.timedelta(hours=1))})
        self.assertEqual(pool.read_strikes(root), {"ema": 2})
        self.assertEqual(pool.load_functions(root)["08000300"], (4, "0" * 40))

    def test_free_order_and_filters(self):
        root = self.root(
            funcs=("08000100", "08000200", "08000300", "08000400"),
            claims={"08000400": "juan " + ts(1), "08000300": "ema " + ts(49)},  # active, expired
            solved=("08000200",),
        )
        # 08000300 (expired claim) is free and smallest; 08000100 biggest.
        self.assertEqual(pool.free(root, NOW, CFG), ["08000300", "08000100"])


if __name__ == "__main__":
    unittest.main()
