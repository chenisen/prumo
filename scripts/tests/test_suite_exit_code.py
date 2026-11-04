"""Exit code 0 does NOT prove a green suite.

An executor that wraps the process — container, runner, wrapper — can hand back 0 with the
suite red. This case came out of production, in 2026-10-29: a repository's suite ran
through a wrapper that does not pass the inner process's code along, returned 0 with the
error count printed in its output, and the review declared `green`.

The texts below are samples of SOME executor's OUTPUT: what is under test is the rule —
read the result, not the wrapper — and which executor produced them does not matter.
"""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from evidence import read_suite
from i18n import t

PATTERN = r"(?m)^(ERRORS!|FAILURES!)|(?:Errors|Failures):\s*[1-9]"
RED_OUTPUT = "..........\n\nERRORS!\nTests: 6767, Assertions: 30207, Errors: 2.\n"
GREEN_OUTPUT = "..........\n\nOK (6765 tests, 30205 assertions)\n"


def run(code, output, truncated=False):
    return {"ran": True, "exit_code": code, "output": output, "truncated": truncated}


def broken_pattern_reason(pattern):
    """The catalogue sentence this exact broken pattern produces, built from the real
    `re.error` — so the assertion names the message and not the wording CPython happens
    to give a bad character class in this version."""
    try:
        re.compile(pattern)
    except re.error as e:
        return t("evidence.invalid_failure_pattern", error=e)
    raise AssertionError("the pattern compiles; this case needs a broken one")


class TestSwallowedExitCode(unittest.TestCase):
    def test_code_zero_with_red_output_is_NOT_green(self):
        state, reason = read_suite(
            run(0, RED_OUTPUT),
            {"failure_pattern": PATTERN, "preexisting_reds": "two credential errors"})
        self.assertEqual(state, "red_not_attributable", reason)

    def test_code_zero_with_red_output_and_no_baseline_is_attributable(self):
        state, _ = read_suite(run(0, RED_OUTPUT), {"failure_pattern": PATTERN})
        self.assertEqual(state, "red_attributable")

    def test_code_zero_with_clean_output_is_green(self):
        state, reason = read_suite(run(0, GREEN_OUTPUT), {"failure_pattern": PATTERN})
        self.assertEqual(state, "green")
        self.assertEqual(reason, t("evidence.green_output_and_code"))


class TestWithNoDeclaredPattern(unittest.TestCase):
    def test_green_with_no_pattern_SAYS_it_rests_on_the_exit_code_alone(self):
        """The absence is legitimate, but it does not pass in silence: the reason names
        the weakness."""
        state, reason = read_suite(run(0, RED_OUTPUT), {})
        self.assertEqual(state, "green")
        self.assertEqual(reason, t("evidence.green_exit_code_only"))

    def test_a_non_zero_code_with_no_pattern_stays_red(self):
        state, _ = read_suite(run(1, RED_OUTPUT), {})
        self.assertEqual(state, "red_attributable")


class TestInvalidPattern(unittest.TestCase):
    def test_a_broken_regex_does_not_become_a_silent_green(self):
        state, reason = read_suite(run(0, RED_OUTPUT), {"failure_pattern": "(["})
        self.assertEqual(state, "not_run")
        self.assertEqual(reason, broken_pattern_reason("(["))


class TestPrecedence(unittest.TestCase):
    def test_did_not_run_beats_everything(self):
        state, _ = read_suite({"ran": False, "reason": "timed out after 900s"},
                              {"failure_pattern": PATTERN})
        self.assertEqual(state, "not_run")

    def test_truncated_output_is_not_a_green_suite(self):
        state, reason = read_suite(run(1, RED_OUTPUT, truncated=True),
                                   {"failure_pattern": PATTERN})
        self.assertEqual(state, "not_run")
        self.assertEqual(reason, t("evidence.output_truncated"))


if __name__ == "__main__":
    unittest.main()
