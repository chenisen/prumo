"""The contract of `deliver`, written before the code: each rule of
`skills/deliver/SKILL.md` has a case here that fails when it is broken.

Until phase 1 lands, this module is RED on purpose: `deliver.py` does not exist yet, and
these cases are what it has to satisfy. The record under test is the one
`schemas/delivery.schema.json` holds; the judge is a pure function over it.
"""
import json
import pathlib
import re
import sys
import unittest

import jsonschema

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from i18n import t, catalogue
import i18n

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCHEMA = json.loads((PRUMO_ROOT / "schemas" / "delivery.schema.json").read_text(encoding="utf-8"))

# The invocation a delivery ends with. A person pastes it; the prumo never runs it.
NEXT = re.compile(r"^/prumo:task-review --repo \S+ --branch \S+ --item \S+$")


def record(**override):
    """A delivery with everything in order — the clean path."""
    base = {
        "item": {"id": "ITEM-1", "kind": "coverage-gap", "source": "tracker"},
        "base": {"name": "main", "sha": "0123abc"},
        "branch": {"name": "ITEM-1-guard", "sha": "4567def"},
        "premises": [{"text": "the guard exists", "state": "holds",
                      "evidence": "src/a.py:41", "central": True}],
        "proof": {"red_on_base": None, "green_on_branch": {"test": "t_guard", "result": "green"},
                  "mutations": [{"file": "src/a.py", "line": 41, "from": ">=", "to": ">",
                                 "test": "t_guard", "result": "red", "reverted": True}]},
        "baseline": {"base_reds": ["t_old"], "branch_reds": ["t_old"], "new_reds": [],
                     "vanished_reds": [], "predicted": 120, "executed": 120},
        "diff": {"files": ["tests/test_a.py"], "added": 30, "removed": 0,
                 "outside_scope": [], "within_budget": True},
        "skipped": [],
        "measured": {"note": "n", "base": None, "suite": None, "lint": None, "mutations": []},
    }
    base.update(override)
    return base


PROFILE = {"profile": "repo-x",
           "delivery": {"kinds": ["coverage-gap"],
                        "budget": {"max_files": 4, "max_lines": 120}}}


def judge(rec=None, profile=PROFILE):
    from deliver import deliver_judge
    return deliver_judge(rec or record(), profile)


class TestTheRecord(unittest.TestCase):
    def test_the_clean_record_validates(self):
        jsonschema.validate(record(), SCHEMA)

    def test_skipped_is_mandatory_even_when_empty(self):
        """Rule 10 — what was not checked is declared, never omitted."""
        rec = record(); del rec["skipped"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(rec, SCHEMA)

    def test_the_record_never_carries_a_verdict(self):
        """Rule 1 — who writes does not approve. The word belongs to task-review."""
        self.assertNotIn("verdict", SCHEMA["properties"])
        self.assertNotIn("APPROVED", json.dumps(SCHEMA))


class TestCleanPath(unittest.TestCase):
    def test_everything_in_order_is_delivered(self):
        self.assertEqual(judge()["state"], "delivered")

    def test_next_is_task_review_and_nothing_else(self):
        """Rule 1 and rule 3 — the end of a delivery is the review's beginning, handed
        over ready to paste, never run."""
        out = judge()
        self.assertRegex(out["next"], NEXT)

    def test_same_record_same_answer(self):
        """Rule 2 — routing is a function, not a judgement. A hundred runs, one answer."""
        answers = {json.dumps(judge(), sort_keys=True) for _ in range(100)}
        self.assertEqual(len(answers), 1)


class TestStops(unittest.TestCase):
    """Rule 4 — every stop has a reason from the catalogue and an owner."""

    def assert_blocked(self, rec, key, owner, **fields):
        out = judge(rec)
        self.assertEqual(out["state"], "blocked", out)
        self.assertEqual(out["reason"], t(key, **fields))
        self.assertEqual(out["owner"], owner)

    def test_a_central_premise_that_fails_blocks_before_anything_is_written(self):
        """Rule 7 — measured before touched. The record carries no diff at all."""
        rec = record(premises=[{"text": "only point of control", "state": "fails",
                                "evidence": "src/b.py:88", "central": True}],
                     diff={"files": [], "added": 0, "removed": 0,
                           "outside_scope": [], "within_budget": True})
        self.assert_blocked(rec, "deliver.blocked.premise_failed", "person",
                            premise="only point of control", evidence="src/b.py:88")

    def test_a_non_central_premise_that_fails_is_recorded_and_does_not_block(self):
        rec = record(premises=[{"text": "aside", "state": "fails",
                                "evidence": "src/b.py:88", "central": False}])
        self.assertEqual(judge(rec)["state"], "delivered")

    def test_a_new_red_by_name_blocks(self):
        """Rule 8 — baseline by name."""
        rec = record(baseline={"base_reds": ["t_old"], "branch_reds": ["t_old", "t_new"],
                               "new_reds": ["t_new"], "vanished_reds": [],
                               "predicted": 120, "executed": 120})
        self.assert_blocked(rec, "deliver.blocked.new_red", "author", names="t_new")

    def test_a_suite_that_did_not_finish_blocks(self):
        """Rule 8 — predicted versus executed. Fewer reds mean nothing if fewer tests ran."""
        rec = record(baseline={"base_reds": [], "branch_reds": [], "new_reds": [],
                               "vanished_reds": [], "predicted": 120, "executed": 97})
        self.assert_blocked(rec, "deliver.blocked.suite_incomplete", "author",
                            executed=97, predicted=120)

    def test_a_suite_nobody_could_count_is_the_profiles_to_fix(self):
        """Rule 8 again — the suite ran, the profile names no progress pattern. The stop
        says so and goes to the profile, never "the suite did not run" to the author."""
        rec = record(baseline={"base_reds": [], "branch_reds": [], "new_reds": [],
                               "vanished_reds": [], "predicted": None, "executed": None},
                     measured={"note": "n", "base": "run-suite-base.txt",
                               "suite": "run-suite-branch.txt", "lint": None, "mutations": []})
        self.assert_blocked(rec, "deliver.blocked.progress_not_declared", "profile")
        counted = dict(PROFILE, checks={"suite": {"progress_pattern": r"(\d+)/(\d+)"}})
        out = judge(rec, profile=counted)
        self.assertEqual((out["state"], out["owner"]), ("blocked", "author"))
        self.assertEqual(out["reason"], t("deliver.blocked.suite_not_run",
                                          reason=t("review.suite_not_run")))

    def test_a_mutation_that_survived_blocks(self):
        rec = record(proof={"red_on_base": None,
                            "green_on_branch": {"test": "t_guard", "result": "green"},
                            "mutations": [{"file": "src/a.py", "line": 41, "from": ">=",
                                           "to": ">", "test": "t_guard", "result": "green",
                                           "reverted": True}]})
        self.assert_blocked(rec, "deliver.blocked.mutation_survived", "author",
                            file="src/a.py", line=41)

    def test_a_proof_the_sceptic_refuted_blocks(self):
        """The mutation reads the suite; the sceptic reads the test. Either alone can be
        fooled — a test on a mock goes red on a mutation the mock does not see."""
        proof = dict(record()["proof"], refutation={"verdict": "does_not_prove",
                                                    "reason": "asserts on the fake"})
        self.assert_blocked(record(proof=proof), "deliver.blocked.refuted", "author",
                            reason="asserts on the fake")

    def test_an_uncertain_sceptic_is_recorded_and_does_not_block(self):
        proof = dict(record()["proof"], refutation={"verdict": "uncertain", "reason": "could not trace"})
        self.assertEqual(judge(record(proof=proof))["state"], "delivered")

    def test_a_plan_that_asks_a_gate_and_has_no_approval_blocks_for_a_person(self):
        plan = {"shape": "hotfix", "depth": "reproduce", "gate": True, "approved_by": None,
                "budget": {"max_files": 2, "max_lines": 60}}
        out = judge(record(plan=plan))
        self.assertEqual((out["state"], out["owner"]), ("blocked", "person"))
        self.assertIn("approve", out["reason"])

    def test_an_approved_plan_lets_the_delivery_through(self):
        plan = {"shape": "hotfix", "depth": "reproduce", "gate": True, "approved_by": "someone",
                "budget": {"max_files": 2, "max_lines": 60}}
        self.assertEqual(judge(record(plan=plan))["state"], "delivered")

    def test_the_budget_the_shape_scaled_is_the_one_the_warning_names(self):
        from deliver import diff_warnings
        diff = {"files": ["a", "b", "c"], "added": 10, "removed": 0,
                "outside_scope": [], "within_budget": False}
        self.assertEqual(diff_warnings(diff, {"max_files": 2, "max_lines": 60}),
                         [t("deliver.warning.budget_exceeded", files=3, lines=10,
                            max_files=2, max_lines=60)])

    def test_a_red_lint_blocks(self):
        """The profile's lint check runs over the tree the writer left; a red there
        would cost a whole review cycle to find."""
        self.assert_blocked(record(lint={"state": "red", "reason": "FAIL tests/test_a.py"}),
                            "deliver.blocked.lint_red", "author", reason="FAIL tests/test_a.py")

    def test_a_mutant_that_turns_a_stays_green_test_red_is_too_wide(self):
        proof = dict(record()["proof"])
        proof["mutations"] = [dict(proof["mutations"][0], stays_green=["t_passage"],
                                   stays_green_result="red")]
        self.assert_blocked(record(proof=proof), "deliver.blocked.mutation_too_wide", "author",
                            file="src/a.py", line=41, test="t_passage")

    def test_a_behaviour_change_without_a_red_on_the_base_is_not_proven(self):
        rec = record(item={"id": "ITEM-1", "kind": "behaviour-change", "source": "tracker"})
        profile = {"profile": "repo-x", "delivery": {"kinds": ["coverage-gap", "behaviour-change"],
                                                   "budget": {"max_files": 4, "max_lines": 120}}}
        out = judge(rec, profile=profile)
        self.assertEqual(out["state"], "blocked")
        self.assertEqual(out["reason"], t("deliver.blocked.no_red_on_base"))

    BEHAVIOUR = {"profile": "repo-x", "delivery": {"kinds": ["behaviour-change"],
                                                   "budget": {"max_files": 4, "max_lines": 120}}}

    def behaviour(self, red_on_base, **override):
        proof = dict(record()["proof"], red_on_base=red_on_base,
                     green_on_branch={"test": "t_x", "result": "green"}, mutations=[])
        return record(item={"id": "ITEM-1", "kind": "behaviour-change", "source": "tracker"}, proof=proof, **override)

    def test_a_test_that_is_green_without_the_change_blocks(self):
        out = judge(self.behaviour({"test": "t_x", "result": "green"}), profile=self.BEHAVIOUR)
        self.assertEqual((out["state"], out["reason"]),
                         ("blocked", t("deliver.blocked.test_not_red_on_base", test="t_x")))

    def test_a_red_on_the_base_read_under_another_name_does_not_block(self):
        """Something went red on the base and one named test was not read among the reds:
        a name is the runner's spelling. The record says which; the delivery stands."""
        rec = self.behaviour({"test": "t_x", "result": "partial", "missing": ["t_x"]})
        jsonschema.validate(rec, SCHEMA)
        self.assertEqual(judge(rec, profile=self.BEHAVIOUR)["state"], "delivered")

    def test_a_writer_that_stopped_is_a_person_s_call(self):
        out = judge(record(writer_blocked="src/a.py:41 already refuses"))
        self.assertEqual((out["state"], out["owner"]), ("blocked", "person"))
        self.assertIn("src/a.py:41 already refuses", out["reason"])

    def test_a_story_with_a_task_not_delivered_blocks_naming_it(self):
        out = judge(record(tasks={"T1": "delivered", "T2": "pending", "T3": "pending"}))
        self.assertEqual((out["state"], out["reason"]),
                         ("blocked", t("deliver.blocked.tasks_pending", tasks="T2, T3")))

    def test_a_red_branch_whose_base_could_not_be_measured_blocks_and_says_why(self):
        base = dict(record()["baseline"], new_reds=None, base="not_run: timed out after 900s")
        out = judge(record(baseline=base))
        self.assertEqual((out["state"], out["reason"]),
                         ("blocked", t("deliver.blocked.base_not_measured", reason="timed out after 900s")))

    def test_the_size_and_the_scope_of_the_suite_are_in_the_record(self):
        rec = record(size={"size": "small", "reason": "2 file(s)"},
                     baseline=dict(record()["baseline"], scope="focused", base="not_needed"))
        jsonschema.validate(rec, SCHEMA)
        self.assertEqual(judge(rec)["state"], "delivered")

    def test_a_behaviour_change_red_on_base_and_green_on_branch_is_delivered(self):
        proof = dict(record()["proof"], red_on_base={"test": "t_x", "result": "red"},
                     green_on_branch={"test": "t_x", "result": "green"}, mutations=[])
        rec = record(item={"id": "ITEM-1", "kind": "behaviour-change", "source": "tracker"}, proof=proof)
        profile = {"profile": "repo-x", "delivery": {"kinds": ["behaviour-change"],
                                                   "budget": {"max_files": 4, "max_lines": 120}}}
        self.assertEqual(judge(rec, profile=profile)["state"], "delivered")

    def test_a_refactor_with_no_guarding_test_stops_for_a_person(self):
        rec = record(item={"id": "ITEM-1", "kind": "refactor", "source": "tracker"},
                     proof={"red_on_base": None, "green_on_branch": None, "mutations": [],
                            "guarding": {"tests": [], "result": "not_run"}})
        profile = {"profile": "repo-x", "delivery": {"kinds": ["refactor"], "budget": {"max_files": 8, "max_lines": 240}}}
        out = judge(rec, profile=profile)
        self.assertEqual((out["state"], out["owner"]), ("blocked", "person"))

    def test_a_refactor_whose_guard_went_red_is_the_author_s(self):
        rec = record(item={"id": "ITEM-1", "kind": "refactor", "source": "tracker"},
                     proof={"red_on_base": None, "green_on_branch": None, "mutations": [],
                            "guarding": {"tests": ["t_old"], "result": "red"}})
        profile = {"profile": "repo-x", "delivery": {"kinds": ["refactor"], "budget": {"max_files": 8, "max_lines": 240}}}
        out = judge(rec, profile=profile)
        self.assertEqual((out["state"], out["owner"]), ("blocked", "author"))

    def test_a_refactor_with_its_guards_green_is_delivered(self):
        rec = record(item={"id": "ITEM-1", "kind": "refactor", "source": "tracker"},
                     proof={"red_on_base": None, "green_on_branch": None, "mutations": [],
                            "guarding": {"tests": ["t_old"], "result": "green"}})
        profile = {"profile": "repo-x", "delivery": {"kinds": ["refactor"], "budget": {"max_files": 8, "max_lines": 240}}}
        self.assertEqual(judge(rec, profile=profile)["state"], "delivered")

    def test_a_file_outside_the_scope_is_a_warning_and_stops_nothing(self):
        from deliver import diff_warnings
        diff = {"files": ["tests/test_a.py", "src/z.py"], "added": 31,
                "removed": 0, "outside_scope": ["src/z.py"], "within_budget": True}
        self.assertEqual(judge(record(diff=diff))["state"], "delivered")
        self.assertEqual(diff_warnings(diff, None),
                         [t("deliver.warning.outside_scope", paths="src/z.py")])

    def test_over_budget_is_a_warning_and_stops_nothing(self):
        from deliver import diff_warnings
        diff = {"files": ["a", "b", "c", "d", "e"], "added": 200, "removed": 0,
                "outside_scope": [], "within_budget": False}
        self.assertEqual(judge(record(diff=diff))["state"], "delivered")
        self.assertEqual(diff_warnings(diff, {"max_files": 4, "max_lines": 120}),
                         [t("deliver.warning.budget_exceeded", files=5, lines=200,
                            max_files=4, max_lines=120)])

    def test_a_diff_inside_the_scope_and_the_budget_warns_of_nothing(self):
        from deliver import diff_warnings
        self.assertEqual(diff_warnings(record()["diff"], {"max_files": 4, "max_lines": 120}), [])

    def test_a_kind_the_profile_does_not_list_is_unsupported(self):
        rec = record(item={"id": "ITEM-1", "kind": "dead-code", "source": "tracker"})
        out = judge(rec)
        self.assertEqual(out["state"], "unsupported")
        self.assertEqual(out["reason"], t("deliver.unsupported.kind", kind="dead-code",
                                          kinds="coverage-gap"))

    def test_a_profile_with_no_delivery_block_is_not_assessable_never_a_default(self):
        """Rule 10 — nothing is assumed about a repository the profile did not describe."""
        from deliver import DeliveryNotAssessable
        with self.assertRaises(DeliveryNotAssessable):
            judge(record(), profile={})


class TestBaselineByName(unittest.TestCase):
    """Rule 8 — the suite is read by NAME and by COUNT OF EXECUTION, from the output,
    through the patterns the profile declares. Which runner printed it does not matter."""

    BASE = "FAIL: t_old (suite.A)\nRan 120 of 120\n"
    BRANCH = "FAIL: t_old (suite.A)\nFAIL: t_new (suite.B)\nRan 120 of 120\n"
    SHORT = "FAIL: t_old (suite.A)\nRan 97 of 120\n"
    CHECK = {"red_name_pattern": r"(?m)^FAIL: (\S+)",
             "progress_pattern": r"(?m)^Ran (\d+) of (\d+)"}

    def test_a_new_red_is_the_set_difference_by_name(self):
        from evidence import compare_reds
        out = compare_reds(self.BASE, self.BRANCH, self.CHECK)
        self.assertEqual(out["new_reds"], ["t_new"])
        self.assertEqual(out["vanished_reds"], [])

    def test_predicted_and_executed_come_from_the_output(self):
        from evidence import compare_reds
        out = compare_reds(self.BASE, self.SHORT, self.CHECK)
        self.assertEqual((out["executed"], out["predicted"]), (97, 120))

    def test_without_a_name_pattern_the_baseline_is_not_assessable(self):
        """A count is not a baseline. No pattern, no names — declared, not guessed."""
        from evidence import compare_reds
        out = compare_reds(self.BASE, self.BRANCH, {})
        self.assertIsNone(out["new_reds"])


class TestTheVoice(unittest.TestCase):
    """Rule 7 of the voice: one idea per sentence, twenty words at most. Held over the
    catalogue, where every sentence a person reads from this capability lives."""

    def test_every_deliver_message_is_twenty_words_or_fewer(self):
        for lang in i18n.available():
            for key, text in catalogue(lang).items():
                if not key.startswith(("deliver.", "run.")):
                    continue
                with self.subTest(language=lang, key=key):
                    for sentence in re.split(r"(?<=[.!?])\s+", str(text)):
                        words = [w for w in sentence.split() if w]
                        self.assertLessEqual(len(words), 20, sentence)



class TestTheSurveyorMeasuresPremisesNotTheChange(unittest.TestCase):
    """Measured on 2026-11-12: a bug item ended in "Expected: the conductor writes
    workflow-result.json". The surveyor listed it as a central premise that fails, and
    the delivery stopped before the writer ran — on the very change it was asked for."""

    def test_the_surveyor_s_prompt_keeps_the_expected_behaviour_out_of_the_premises(self):
        agent = (PRUMO_ROOT / "agents" / "premise-surveyor.md").read_text(encoding="utf-8")
        self.assertIn("is not a premise. It is the change, and it fails on the base by definition.", agent)
        self.assertIn("its proof is `proving_tests`, red on the base.", agent)


if __name__ == "__main__":
    unittest.main()


class TestTheBranchName(unittest.TestCase):
    """The team names the branch on the item; a slug is only what stands in when
    nobody did."""
    BLOCK = {"branch_format": "{item}-{slug}"}

    def test_the_invocation_s_branch_wins(self):
        from deliver import branch_name
        self.assertEqual(branch_name(self.BLOCK, "I-1", "# t\nBranch: named\n", "given"), "given")

    def test_the_item_s_branch_line_is_read(self):
        from deliver import branch_name
        self.assertEqual(branch_name(self.BLOCK, "I-1", "# Title\n\nCard I-1\nBranch: I-1-prove-the-guard\n", None),
                         "I-1-prove-the-guard")

    def test_without_one_the_title_is_slugged(self):
        from deliver import branch_name
        self.assertEqual(branch_name(self.BLOCK, "I-1", "# Prove the guard\n", None), "I-1-prove-the-guard")


class TestATaskSCriteriaAreNotPremises(unittest.TestCase):
    """The first story task measured (2026-11-12) was blocked before a line was written:
    the surveyor read the task's target state as a failing central premise. A task is no
    longer surveyed — the person approved the design, and the writer reads the code it
    changes — and the brief still says what its bullets are."""

    def test_the_task_brief_says_so_to_its_writer(self):
        workflow = (PRUMO_ROOT / "workflows" / "deliver.js").read_text(encoding="utf-8")
        self.assertIn("The task\\'s bullets are its CRITERIA", workflow)
        self.assertIn("name NO mutation", workflow)
        self.assertIn("return `blocked` with the address", workflow)
        writer = (PRUMO_ROOT / "agents" / "change-writer.md").read_text(encoding="utf-8")
        self.assertIn("## A task of a story", writer)
        self.assertIn("A premise the code\ndenies is a person's call", writer)


class TestABlockedAttemptIsWrittenAgainWithItsReason(unittest.TestCase):
    def test_the_write_stage_carries_the_previous_verdict(self):
        workflow = (PRUMO_ROOT / "workflows" / "deliver.js").read_text(encoding="utf-8")
        self.assertIn("A PREVIOUS ATTEMPT AT THIS ITEM WAS BLOCKED", workflow)
        self.assertIn("input.previous.reason", workflow)
        skill = (PRUMO_ROOT / "skills" / "deliver" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## A blocked attempt, written again", skill)


class TheSecondReadingOfTheFirstStory(unittest.TestCase):
    """What the first story (2026-11-12) measured, held in the briefs: a writer that
    left a test double silent on a typed method, a rewrite that listed one file of six,
    a write that undid the task before it, a fix round whose mutants pointed at the
    lines the fix rewrote."""

    WORKFLOW = (PRUMO_ROOT / "workflows" / "deliver.js").read_text(encoding="utf-8")
    WRITER = (PRUMO_ROOT / "agents" / "change-writer.md").read_text(encoding="utf-8")
    SCEPTIC = (PRUMO_ROOT / "agents" / "proof-skeptic.md").read_text(encoding="utf-8")
    SURVEYOR = (PRUMO_ROOT / "agents" / "premise-surveyor.md").read_text(encoding="utf-8")

    def test_a_typed_test_double_returns_a_value_of_its_type(self):
        self.assertIn("declared return type returns a value of that type", self.WORKFLOW)
        self.assertIn("a test double of a method with a declared return type", self.WRITER)

    def test_the_code_is_clean_when_written_and_keeps_the_file_s_conventions(self):
        self.assertIn("The code you write is clean on the first pass", self.WORKFLOW)
        self.assertIn("only where a test you write depends on it", self.WORKFLOW)
        self.assertIn("## The code you write is clean on the first pass", self.WRITER)
        self.assertIn("**one level of nesting in what you add**", self.WRITER)
        self.assertIn("**the file's conventions stay.**", self.WRITER)
        reviewer = (PRUMO_ROOT / "agents" / "architecture-reviewer.md").read_text(encoding="utf-8")
        self.assertIn("**Added code that does not read**", reviewer)
        self.assertIn("**A convention the change switched without a test that needs it**", reviewer)

    def test_a_second_attempt_lists_every_file_since_the_task_base(self):
        self.assertIn("lists EVERY file changed since the task base", self.WORKFLOW)
        self.assertIn("This is a SECOND attempt on the same tree", self.WORKFLOW)
        self.assertIn("**A second attempt**", self.WRITER)

    def test_the_writer_reads_the_story_s_earlier_tasks(self):
        """The writer of each task reads the tasks before it; the sceptic no longer reads
        a story task by task — the review reads the whole branch, once."""
        self.assertIn("EARLIER TASKS OF THIS STORY", self.WORKFLOW)
        self.assertIn("earlier: [...earlier]", self.WORKFLOW)
        self.assertIn("undoes an earlier task of the same story", self.WRITER)
        self.assertIn("nor about each task of a story", self.SCEPTIC)

    def test_any_behaviour_change_names_no_mutation(self):
        self.assertIn("This item changes behaviour: name NO mutation", self.WORKFLOW)
        self.assertIn("if (item.kind === 'behaviour-change') measure.mutations = []", self.WORKFLOW)
        self.assertIn("Name\n  **no** mutation", self.SURVEYOR)



class TestATestBesideTheCodeIsNotProduction(unittest.TestCase):
    """Red before green restores the base's production files under the new test. A
    repository that keeps its tests inside the source root (`src/**/__tests__`) had the
    proving test restored with the code, and the red could never show."""

    BLOCK = {"source_roots": ["src"], "test_roots": ["src/**/__tests__"]}

    def test_a_test_inside_a_source_root_stays_out_of_production(self):
        from deliver import production_files
        code = "src/app/plans/ActionsPlan/index.tsx"
        spec = "src/app/plans/ActionsPlan/__tests__/actionsPlan.spec.tsx"
        self.assertEqual(production_files([code, spec], self.BLOCK), [code])

    def test_separate_roots_keep_their_split(self):
        from deliver import production_files
        block = {"source_roots": ["app"], "test_roots": ["tests/Unit"]}
        self.assertEqual(production_files(["app/A.php", "tests/Unit/ATest.php"], block),
                         ["app/A.php"])


class TestTheWriterExecutes(unittest.TestCase):
    """Until 3.0 the writer had no shell: it wrote blind, a script ran the whole suite
    after it, and a red sent the item back for a second write. Six of the first story's
    thirteen tasks were written twice. The writer now runs what it touches; the whole
    suite and the environment for showing a change stay out of its hands."""

    WRITER = (PRUMO_ROOT / "agents" / "change-writer.md").read_text(encoding="utf-8")
    WORKFLOW = (PRUMO_ROOT / "workflows" / "deliver.js").read_text(encoding="utf-8")

    def test_the_writer_has_a_shell_and_the_readers_do_not(self):
        self.assertIn("tools: Read, Write, Edit, Grep, Glob, Bash", self.WRITER)
        for reader in ("premise-surveyor", "proof-skeptic", "task-planner"):
            text = (PRUMO_ROOT / "agents" / f"{reader}.md").read_text(encoding="utf-8")
            self.assertIn("tools: Read, Grep, Glob\n", text, reader)

    def test_it_runs_the_tests_it_touches_and_never_the_whole_suite(self):
        self.assertIn("See it **red**", self.WRITER)
        self.assertIn("- the whole suite — it is the script's, once", self.WRITER)
        self.assertIn("a script that writes or patches code", self.WRITER)
        self.assertIn("Never the whole suite, never an environment for showing the change", self.WORKFLOW)
        self.assertIn("Read a result in the OUTPUT, never in the exit code", self.WORKFLOW)

    def test_an_item_with_no_gate_is_measured_and_written_in_one_call(self):
        self.assertIn("if (stage === 'deliver')", self.WORKFLOW)
        self.assertIn("const refutation = sceptical(size, written) ? await refute(measure, written) : null",
                      self.WORKFLOW)

    def test_the_size_is_read_again_after_the_write_by_the_lines_that_landed(self):
        """Sized by its files before the write, an item that lands past the small limit is
        a standard one, and the sceptic reads it — in the one-call stage and in `write`."""
        self.assertIn("const size = written.blocked ? sizeOf(measure.files) : sizeWritten(measure, written)",
                      self.WORKFLOW)
        self.assertIn("sceptical(sizeWritten(measure, written), written)", self.WORKFLOW)
        self.assertIn("(written.lines_changed ?? 0) > smallLimit.max_lines ? 'standard' : 'small'", self.WORKFLOW)
        self.assertIn("lines_changed: { type: ['integer', 'null'] }", self.WORKFLOW)
        self.assertIn("`lines_changed` is read, never estimated", self.WRITER)

    def test_a_story_s_tasks_are_written_in_one_call_and_committed_by_their_writers(self):
        self.assertIn("if (stage === 'tasks')", self.WORKFLOW)
        self.assertIn("commit it yourself, with a pathspec and never `git add -A`", self.WORKFLOW)
        self.assertIn("Do NOT commit: the script commits a delivery that stands.", self.WORKFLOW)


class TestTheEvidenceIsCollectedOnceAtTheEnd(unittest.TestCase):
    COLLECTOR = (PRUMO_ROOT / "agents" / "evidence-collector.md").read_text(encoding="utf-8")
    WORKFLOW = (PRUMO_ROOT / "workflows" / "deliver.js").read_text(encoding="utf-8")

    def test_the_collector_runs_last_and_stops_nothing(self):
        self.assertIn("**last, once per item**", self.COLLECTOR)
        self.assertIn("raise the environment more than once, or leave it up", self.COLLECTOR)
        self.assertIn("change a file of the repository", self.COLLECTOR)

    def test_without_a_declared_way_the_stage_raises_nothing(self):
        self.assertIn("if (!input.evidence) {", self.WORKFLOW)
        self.assertIn("return { stage, evidence: null }", self.WORKFLOW)


def incident(**override):
    """An incident fixed at its cause and read once — the clean path of the shape."""
    block = {"symptom": "the route answers 500 for a customer with no plan",
             "root_cause": {"what": "the plan is read before it is checked for null",
                            "where": "src/a.py:41", "introduced_by": "abc1234 read the plan earlier"},
             "fix": "check the plan before reading it",
             "verify": ["GET /plan for a customer with no plan answers 200"],
             "follow_ups": [{"what": "the same read has no timeout", "where": "src/a.py:60", "by": "reader"}],
             "reading": {"verdict": "resolves", "reason": "the null plan can no longer reach the read",
                         "cause_read": "src/a.py:41", "evidence": []}}
    block.update(override)
    return block


def incident_record(**override):
    proof = {"red_on_base": {"test": "t_no_plan", "result": "red"},
             "green_on_branch": {"test": "t_no_plan", "result": "green"}, "mutations": []}
    plan = {"shape": "incident", "depth": "root-cause", "gate": False, "approved_by": None, "budget": None}
    return record(item={"id": "ITEM-1", "kind": "behaviour-change", "source": "tracker"},
                  premises=[], proof=proof, plan=plan, incident=incident(**override))


INCIDENT_PROFILE = {"profile": "repo-x", "delivery": {"kinds": ["behaviour-change"]}}


class TestAnIncidentIsHeldOnlyByItsCauseAndByARegression(unittest.TestCase):
    """The seven findings that held the first incident for three review rounds said the
    test could prove more or the new code could be sturdier. None said the fix missed the
    cause or broke what worked — the only two things worth keeping production failing for."""

    def judge(self, **override):
        from deliver import deliver_judge
        return deliver_judge(incident_record(**override), INCIDENT_PROFILE)

    def test_the_record_of_an_incident_validates(self):
        jsonschema.validate(incident_record(), SCHEMA)

    def test_fixed_at_its_cause_and_read_once_it_is_delivered(self):
        self.assertEqual(self.judge()["state"], "delivered")

    def test_a_fix_with_no_address_for_its_cause_does_not_stand(self):
        out = self.judge(root_cause={"what": "something upstream", "where": None, "introduced_by": None})
        self.assertEqual((out["state"], out["owner"]), ("blocked", "person"))
        self.assertEqual(out["reason"], t("deliver.blocked.incident_no_root_cause"))

    def test_a_symptom_that_survives_the_fix_holds_it_with_the_address(self):
        reading = {"verdict": "does_not_resolve", "reason": "the batch route reads the plan the same way",
                   "evidence": [{"file": "src/b.py", "line": 12, "excerpt": "plan.id"}]}
        out = self.judge(reading=reading)
        self.assertEqual((out["state"], out["owner"]), ("blocked", "author"))
        self.assertIn("src/b.py:12", out["reason"])
        self.assertIn("the batch route", out["reason"])

    def test_a_fix_that_breaks_what_worked_holds_it(self):
        reading = {"verdict": "regresses", "reason": "the export now skips customers with a plan",
                   "evidence": [{"file": "src/export.py", "line": 7}]}
        out = self.judge(reading=reading)
        self.assertEqual(out["state"], "blocked")
        self.assertEqual(out["reason"], t("deliver.blocked.incident_regresses", reason=reading["reason"],
                                          where="src/export.py:7"))

    def test_an_uncertain_reading_is_shown_and_holds_nothing(self):
        out = self.judge(reading={"verdict": "uncertain", "reason": "could not trace the second caller"})
        self.assertEqual(out["state"], "delivered")

    def test_a_follow_up_is_never_a_reason_to_hold(self):
        many = [{"what": f"sturdier {n}", "where": None, "by": "reader"} for n in range(9)]
        self.assertEqual(self.judge(follow_ups=many)["state"], "delivered")

    def test_a_fix_nobody_read_is_not_an_incident_s_delivery(self):
        out = self.judge(reading=None)
        self.assertEqual((out["state"], out["reason"]), ("blocked", t("deliver.blocked.incident_not_read")))

    def test_it_still_owes_the_test_that_reproduces_it(self):
        rec = incident_record()
        rec["proof"]["red_on_base"] = None
        from deliver import deliver_judge
        out = deliver_judge(rec, INCIDENT_PROFILE)
        self.assertEqual(out["reason"], t("deliver.blocked.no_red_on_base"))

    def test_it_ends_at_a_person_s_push_and_the_review_comes_after(self):
        out = self.judge()
        self.assertFalse(NEXT.match(out["next"]), "an incident does not end at a review's invocation")
        self.assertIn("ITEM-1-guard", out["next"])
        self.assertIn("/prumo:task-review --repo repo-x --branch ITEM-1-guard --item ITEM-1", out["next"])

    def test_same_record_same_answer(self):
        self.assertEqual({json.dumps(self.judge(), sort_keys=True) for _ in range(50)}.__len__(), 1)


class TestTheIncidentsRecordIsBuiltFromWhatTheAgentsReturned(unittest.TestCase):
    def test_the_follow_ups_of_both_agents_are_kept_each_signed(self):
        from deliver import incident_record as build
        out = build({"symptom": "s", "root_cause": {"what": "w", "where": "src/a.py:1", "introduced_by": None},
                     "fix": "f", "verify": ["v"], "follow_ups": [{"what": "seen by the fixer", "where": None}]},
                    {"verdict": "resolves", "reason": "r", "evidence": [],
                     "follow_ups": [{"what": "seen by the reader", "where": "src/a.py:9"}]})
        self.assertEqual([(f["what"], f["by"]) for f in out["follow_ups"]],
                         [("seen by the fixer", "fixer"), ("seen by the reader", "reader")])
        jsonschema.validate(out, SCHEMA["properties"]["incident"])

    def test_what_the_workflow_did_not_return_is_empty_never_invented(self):
        from deliver import incident_record as build
        out = build(None, None)
        self.assertIsNone(out["root_cause"]["where"])
        self.assertIsNone(out["reading"])
        jsonschema.validate(out, SCHEMA["properties"]["incident"])


class TestTheIncidentStageIsOneCallAndTheReaderIsBlind(unittest.TestCase):
    WORKFLOW = (PRUMO_ROOT / "workflows" / "deliver.js").read_text(encoding="utf-8")
    FIXER = (PRUMO_ROOT / "agents" / "incident-fixer.md").read_text(encoding="utf-8")
    READER = (PRUMO_ROOT / "agents" / "incident-reader.md").read_text(encoding="utf-8")

    def stage(self):
        return self.WORKFLOW.split("if (stage === 'incident') {", 1)[1].split("\nif (stage === ", 1)[0]

    def test_the_fixer_has_a_shell_and_the_reader_does_not(self):
        self.assertIn("tools: Read, Write, Edit, Grep, Glob, Bash", self.FIXER)
        self.assertIn("tools: Read, Grep, Glob\n", self.READER)

    def test_no_surveyor_no_plan_and_no_sceptic_stand_in_the_stage(self):
        stage = self.stage()
        for absent in ("measureItem", "refute(", "writeItem"):
            self.assertNotIn(absent, stage, absent)
        self.assertIn("fixIncident", stage)
        self.assertIn("readFix", stage)

    def test_the_reader_is_handed_the_change_and_never_the_fixer_s_account_of_the_cause(self):
        reader = self.WORKFLOW.split("async function readFix(fixed) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("incident.diff", reader)
        for told in ("root_cause", "fixed.fix", "fixed.symptom", "follow_ups"):
            self.assertNotIn(told, reader.split("schema: READING_SCHEMA", 1)[0].replace("`follow-up`", ""), told)

    def test_a_fixer_that_stopped_is_not_read(self):
        self.assertIn("const reading = fixed.blocked ? null : await readFix(fixed)", self.stage())

    def test_the_stage_returns_what_conclude_reads_of_any_delivery(self):
        self.assertIn("return { stage, measure, written, refutation: null, incident, reading }", self.stage())

    def test_the_reader_holds_two_answers_and_everything_else_is_a_follow_up(self):
        self.assertIn("**Nothing else holds an incident.**", self.READER)
        self.assertIn('"resolves" | "does_not_resolve" | "regresses" | "uncertain"', self.READER)

    def test_the_fixer_goes_to_the_cause_and_stops_when_it_is_not_in_the_repository(self):
        self.assertIn("not the line where it\n   surfaces", self.FIXER)
        self.assertIn("the cause is **outside this repository**", self.FIXER)
        self.assertIn("The whole suite;", self.FIXER)

    def test_both_readings_are_asked_of_the_heavy_tier(self):
        self.assertIn("'incident-fixer': 'heavy'", self.WORKFLOW)
        self.assertIn("'incident-reader': 'heavy'", self.WORKFLOW)

