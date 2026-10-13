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



def claim_pr(root, addrs, author="juan", user=None, when=None, status="A"):
    changes = [(status, "pool/claims/" + a) for a in addrs]
    contents = {p: "%s %s\n" % (user or author, when or ts()) for _, p in changes}
    return pool.validate(root, changes, contents, author, NOW, NOW, CFG, state_only=True)


class ClassifyTest(unittest.TestCase):
    def test_kinds(self):
        c = pool.classify
        self.assertEqual(c([("A", "pool/claims/08000100"), ("A", "pool/claims/08000200")]), "claim")
        self.assertEqual(c([("M", "pool/claims/08000100")]), "claim")
        self.assertEqual(c([("D", "pool/claims/08000100")]), "release")
        self.assertEqual(c([("A", "src/func_08000100.c"), ("D", "pool/claims/08000100")]), "solve")

    def test_manual(self):
        c = pool.classify
        for changes in (
            [],
            [("A", "tools/pool.py")],
            [("A", "pool/claims/08000100"), ("D", "pool/claims/08000200")],
            [("A", "pool/claims/08000100"), ("A", "AGENTS.md")],
            [("A", "src/func_08000100.c")],
            [("A", "src/func_08000100.c"), ("D", "pool/claims/08000200")],
            [("M", "src/func_08000100.c"), ("D", "pool/claims/08000100")],
            [("A", "src/func_08000100.c"), ("D", "pool/claims/08000100"), ("A", "src/func_08000200.c")],
            [("A", "pool/claims/0800010a")],
            [("A", "pool/strikes/juan")],
        ):
            self.assertEqual(c(changes), "manual", changes)


class ClaimTest(PoolTest):
    def test_ok(self):
        self.assertEqual(claim_pr(self.root(), ["08000100", "08000200"]), ("claim", []))

    def test_author_case_insensitive(self):
        self.assertEqual(claim_pr(self.root(), ["08000100"], author="Juan", user="juan")[1], [])

    def test_errors(self):
        root = self.root(
            claims={"08000200": "ema " + ts(1)}, solved=("08000300",), strikes={"banned": 3}
        )
        for kwargs, addrs, needle in (
            ({"user": "ema"}, ["08000100"], "must be the PR author"),
            ({"when": ts(-1)}, ["08000100"], "in the future"),
            ({"when": ts(1)}, ["08000100"], "minutes of the PR"),
            ({}, ["0800FFFC"], "not in functions.csv"),
            ({}, ["08000300"], "already solved"),
            ({}, ["08000200"], "claimed by ema"),
            ({"author": "banned"}, ["08000100"], "banned"),
        ):
            kind, errs = claim_pr(root, addrs, **kwargs)
            self.assertTrue(any(needle in e for e in errs), (kwargs, addrs, errs))

    def test_bad_content(self):
        root = self.root()
        changes = [("A", "pool/claims/08000100")]
        _, errs = pool.validate(root, changes, {changes[0][1]: "juan\n"}, "juan", NOW, NOW, CFG, True)
        self.assertTrue(any("content must be" in e for e in errs), errs)

    def test_reclaim_expired(self):
        root = self.root(claims={"08000100": "ema " + ts(49)})
        self.assertEqual(claim_pr(root, ["08000100"], status="M")[1], [])

    def test_max_claims(self):
        root = self.root(claims={"08000100": "juan " + ts(1)})
        _, errs = claim_pr(root, ["08000200", "08000300"])
        self.assertTrue(any("max_claims" in e for e in errs), errs)
        # an expired claim does not count as held
        root = self.root(claims={"08000100": "juan " + ts(49)})
        self.assertEqual(claim_pr(root, ["08000200", "08000300"])[1], [])


class ReleaseSolveTest(PoolTest):
    def test_release(self):
        root = self.root(claims={"08000100": "juan " + ts(1), "08000200": "ema " + ts(1)})
        rel = lambda a: pool.validate(root, [("D", "pool/claims/" + a)], {}, "juan", NOW, NOW, CFG, True)
        self.assertEqual(rel("08000100"), ("release", []))
        self.assertTrue(rel("08000200")[1])

    def test_solve_state(self):
        root = self.root(claims={"08000100": "juan " + ts(1), "08000200": "juan " + ts(49), "08000300": "ema " + ts(1)})

        def solve(a, author="juan"):
            changes = [("A", "src/func_%s.c" % a), ("D", "pool/claims/" + a)]
            return pool.validate(root, changes, {changes[0][1]: ""}, author, NOW, NOW, CFG, True)

        self.assertEqual(solve("08000100"), ("solve", []))
        self.assertTrue(any("expired" in e for e in solve("08000200")[1]))
        self.assertTrue(any("not yours" in e for e in solve("08000300")[1]))


class ExpireTest(PoolTest):
    def test_expire(self):
        root = self.root(claims={"08000100": "Juan " + ts(49), "08000200": "ema " + ts(1)}, strikes={"juan": 1})
        self.assertEqual(pool.expire(root, NOW, CFG), [("08000100", "Juan")])
        self.assertEqual(sorted(pool.read_claims(root)), ["08000200"])
        self.assertEqual(pool.read_strikes(root), {"juan": 2})

if __name__ == "__main__":
    unittest.main()
