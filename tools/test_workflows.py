import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PoolWorkflowTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, ".github", "workflows", "pool.yml")) as f:
            self.wf = f.read()

    def test_pins_the_verified_head(self):
        # fetching refs/pull/N/head lets a push after verify be merged unverified
        self.assertNotIn("refs/pull/", self.wf)
        self.assertIn("github.event.pull_request.head.sha", self.wf)
        self.assertRegex(self.wf, r"gh pr merge [^\n]*--match-head-commit \"\$SHA\"")

    def test_merge_message_is_not_attacker_text(self):
        merge = re.search(r"gh pr merge [^\n]*", self.wf).group()
        self.assertIn("--subject", merge)
        self.assertIn('--body ""', merge)

    def test_merge_retries_recheck_against_new_main(self):
        # a lost race retries, but each attempt re-validates against main as it is then
        loop = re.search(r"for attempt in 1 2 3; do\n(.*?)\n\s*done", self.wf, re.S)
        self.assertIsNotNone(loop)
        body = loop.group(1)
        self.assertLess(body.index("git reset -q --hard"), body.index("pool.py validate"))
        self.assertLess(body.index("pool.py validate"), body.index("gh pr merge"))

    def test_token_only_kept_for_private_repos(self):
        # a public repo fetches anonymously; a private one needs the job token to fetch the PR
        self.assertEqual(self.wf.count("persist-credentials: ${{ github.event.repository.private }}"), 2)
        self.assertNotIn("persist-credentials: false", self.wf)

if __name__ == "__main__":
    unittest.main()
