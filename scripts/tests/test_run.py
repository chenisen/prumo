"""Rule 5 — the whole route runs only on the person's own words, once.

RED until phase 4. Two things hold it: the skill is closed to the model's invocation, so
every argument is typed by a person; and the script refuses to start without the ask
written to a file, so a resumed or compacted session reads the authorisation back
instead of remembering it.
"""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SKILL = PRUMO_ROOT / "skills" / "run" / "SKILL.md"


class TestTheRouteIsThePersons(unittest.TestCase):
    def test_the_skill_is_closed_to_the_model(self):
        self.assertTrue(SKILL.exists(), "skills/run/SKILL.md")
        head = SKILL.read_text(encoding="utf-8").split("---")[1]
        self.assertRegex(head, re.compile(r"^disable-model-invocation:\s*true\s*$", re.M))

    def test_run_refuses_without_the_ask_on_disk(self):
        from run import start, AskMissing
        with self.assertRaises(AskMissing):
            start(repo="r", item="ITEM-1", ask_file=None)

    def test_run_refuses_an_empty_ask(self):
        """A file that is there and empty does not count as read — the same rule the
        review holds over the item."""
        import tempfile
        from run import start, AskMissing
        with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
            f.flush()
            with self.assertRaises(AskMissing):
                start(repo="r", item="ITEM-1", ask_file=f.name)


if __name__ == "__main__":
    unittest.main()


class TestEveryRoutingDecisionIsReadFromDisk(unittest.TestCase):
    """Rule 2 — `run.py next` composes nothing: each answer is what the two packages
    hold, and the same disk gives the same answer."""

    def setUp(self):
        import json, tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "item.md").write_text("# x\n", encoding="utf-8")
        self.deliver = self.root / "d"; self.deliver.mkdir()
        self.review = self.root / "r"; self.review.mkdir()
        self.run = {"repo": "repo-x", "item": "ITEM-1",
                    "deliver_package": str(self.deliver), "review_package": str(self.review)}
        self.json = json

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, where, name, data):
        (where / name).write_text(self.json.dumps(data), encoding="utf-8")

    def decide(self):
        from run import decide
        return decide(self.run, self.root)

    def test_nothing_on_disk_asks_for_the_delivery_to_be_prepared(self):
        out = self.decide()
        self.assertEqual(out["kind"], "invoke")
        self.assertTrue(out["text"].startswith("deliver.py prepare --repo repo-x --item ITEM-1"))

    def test_a_prepared_delivery_asks_for_the_measure_stage(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.assertIn("stage: 'measure'", self.decide()["text"])

    def test_a_measured_delivery_asks_for_the_plan_then_the_write_stage(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "measure.json", {"premises": [{"central": True, "state": "holds"}]})
        self.assertTrue(self.decide()["text"].startswith("deliver.py plan"))
        self.put(self.deliver, "plan.json", {"gate": False, "digest": "d"})
        self.assertIn("stage: 'write'", self.decide()["text"])

    def test_a_shape_with_a_gate_stops_until_a_person_approves_the_plan_as_it_stands(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "measure.json", {"premises": []})
        self.put(self.deliver, "plan.json", {"gate": True, "digest": "d1"})
        out = self.decide()
        self.assertEqual((out["kind"], out["class"]), ("stop", "plan"))
        self.assertIn("/prumo:approve", out["text"])
        self.put(self.deliver, "approval.json", {"by": "someone", "digest": "stale"})
        self.assertEqual(self.decide()["class"], "plan", "an approval of another measurement does not count")
        self.put(self.deliver, "approval.json", {"by": "someone", "digest": "d1"})
        self.assertIn("stage: 'write'", self.decide()["text"])

    def test_the_shape_travels_into_the_prepare_invocation(self):
        self.run["shape"] = "bug"
        self.assertIn("--shape bug", self.decide()["text"])

    def test_a_central_premise_that_fails_skips_the_write_and_concludes(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "measure.json", {"premises": [{"central": True, "state": "fails"}]})
        self.put(self.deliver, "plan.json", {"gate": True, "digest": "d"})
        out = self.decide()
        self.assertTrue(out["text"].startswith("deliver.py conclude"), out)
        self.assertNotIn("--written", out["text"])

    def test_a_written_delivery_concludes_with_every_return_it_has(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "measure.json", {"premises": []})
        self.put(self.deliver, "plan.json", {"gate": False, "digest": "d"})
        self.put(self.deliver, "written.json", {})
        self.put(self.deliver, "refutation.json", {})
        out = self.decide()["text"]
        self.assertIn("--written", out); self.assertIn("--refutation", out)

    def test_a_blocked_delivery_is_a_stop_with_its_class_and_reason(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "blocked", "reason": "new red: t_x",
                                                 "branch": {"name": "b"}})
        out = self.decide()
        self.assertEqual((out["kind"], out["class"], out["text"]), ("stop", "blocked", "new red: t_x"))

    def test_a_delivered_branch_hands_the_review_its_prepare(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "ITEM-1-b"}})
        out = self.decide()["text"]
        self.assertTrue(out.startswith("review.py prepare --repo repo-x --branch ITEM-1-b --item ITEM-1"))

    def test_a_prepared_review_asks_for_the_workflow_then_the_conclude(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "b"}})
        self.put(self.review, "manifest.json", {})
        import run
        self.assertIn(f"name: '{run._wf('task-review')}'", self.decide()["text"])
        self.put(self.review, "findings.json", [])
        self.assertTrue(self.decide()["text"].startswith("review.py conclude"))

    def test_a_verdict_ends_the_run(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "b"}})
        self.put(self.review, "manifest.json", {})
        self.put(self.review, "verdict.json", {"verdict": "APPROVED"})
        self.assertEqual(self.decide()["class"], "approved")
        self.put(self.review, "verdict.json", {"verdict": "INTERVENTION_REQUIRED"})
        self.assertEqual(self.decide()["class"], "intervention")

    def test_a_verdict_releases_a_standing_worktree_before_the_stop(self):
        workdir = self.root / "wt"; workdir.mkdir()
        self.put(self.deliver, "deliver-manifest.json", {"in_worktree": True, "workdir": str(workdir)})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "b"}})
        self.put(self.review, "manifest.json", {})
        self.put(self.review, "verdict.json", {"verdict": "APPROVED"})
        out = self.decide()
        self.assertEqual((out["kind"], out["text"]), ("invoke", f"deliver.py release --package {self.deliver}"))
        workdir.rmdir()
        self.assertEqual(self.decide()["class"], "approved")

    def test_a_blocked_delivery_keeps_its_worktree_for_a_person_to_read(self):
        workdir = self.root / "wt"; workdir.mkdir()
        self.put(self.deliver, "deliver-manifest.json", {"in_worktree": True, "workdir": str(workdir)})
        self.put(self.deliver, "delivery.json", {"state": "blocked", "reason": "r"})
        self.assertEqual(self.decide()["class"], "blocked")
        self.assertTrue(workdir.exists())

    def test_an_intervention_with_an_author_fix_route_hands_the_fix_door(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "b"}})
        self.put(self.review, "manifest.json", {})
        self.put(self.review, "verdict.json", {"verdict": "INTERVENTION_REQUIRED", "routes": [
            {"type": "author_fix", "items": ["COR-1"], "owner": "author", "closes_when": "x"}]})
        out = self.decide()
        self.assertEqual(out["class"], "intervention")
        self.assertIn(f"deliver.py prepare --repo repo-x --fix {self.review}", out["text"])

    def test_status_says_where_the_run_stands_and_writes_nothing(self):
        """The public contract for a runner or an app: the same step `next` would give,
        with what the run is, and orchestration.md untouched however often it is asked."""
        from run import status, STATUS_VERSION
        (self.root / "run.json").write_text(self.json.dumps(self.run), encoding="utf-8")
        log = self.root / "orchestration.md"
        before = log.read_text(encoding="utf-8") if log.exists() else None
        first, second = status(self.root), status(self.root)
        self.assertEqual(first, second)
        self.assertEqual(first["version"], STATUS_VERSION)
        self.assertEqual((first["repo"], first["item"]), ("repo-x", "ITEM-1"))
        self.assertEqual(first["step"]["kind"], self.decide()["kind"])
        self.assertEqual(first["step"]["text"], self.decide()["text"])
        after = log.read_text(encoding="utf-8") if log.exists() else None
        self.assertEqual(before, after)

    def test_status_of_every_run_keeps_a_broken_one_as_a_stop(self):
        from run import status_all
        good = self.root / "good"; good.mkdir()
        (good / "run.json").write_text(self.json.dumps(self.run), encoding="utf-8")
        broken = self.root / "broken"; broken.mkdir()
        (broken / "run.json").write_text("{not json", encoding="utf-8")
        out = {pathlib.Path(o["package"]).name: o for o in status_all(self.root)}
        self.assertEqual(set(out), {"good", "broken"})
        self.assertEqual(out["broken"]["step"]["kind"], "stop")
        self.assertEqual(out["broken"]["step"]["class"], "blocked")
        self.assertEqual(out["good"]["step"]["kind"], "invoke")

    def test_the_same_disk_gives_the_same_answer(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        answers = {self.decide()["text"] for _ in range(50)}
        self.assertEqual(len(answers), 1)

    def test_the_log_ends_on_the_stop_line_and_never_repeats_a_step(self):
        from run import log, next_step
        self.json.dump(self.run, open(self.root / "run.json", "w"))
        next_step(self.root); next_step(self.root)
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "unsupported", "reason": "kind",
                                                 "branch": {"name": "b"}})
        next_step(self.root)
        lines = (self.root / "orchestration.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("unsupported", lines[-1])


class TestTheRouteOverRealPackages(unittest.TestCase):
    """The decision table above reads file names; this reads the files the spine
    actually writes. A delivery and a review are run for real over a throwaway
    repository, and `run.py next` has to find its way through what they left."""

    def setUp(self):
        from tests.test_deliver_backbone import Spine
        self.spine = Spine("test_prepare_cuts_the_branch_reads_the_baseline_and_marks")
        self.spine.setUp()
        self.root, self.repo = self.spine.root, self.spine.repo

    def tearDown(self):
        self.spine.tearDown()

    def test_next_walks_from_nothing_to_the_verdict(self):
        import argparse, contextlib, io, json
        from tests.test_deliver_backbone import PROVING_TEST, MEASURE
        import run, review
        pkg = run.start("repo-x", "ITEM-1", str(self.root / "item.md"),
                        item_file=str(self.root / "item.md"), package=str(self.root / "run"))
        self.assertTrue(run.next_step(pkg)["text"].startswith("deliver.py prepare"))

        deliver_pkg = self.spine.prepare()
        run.bind(pkg, "deliver", str(deliver_pkg))
        self.assertIn("stage: 'measure'", run.next_step(pkg)["text"])
        (deliver_pkg / "measure.json").write_text(json.dumps(MEASURE), encoding="utf-8")
        self.assertTrue(run.next_step(pkg)["text"].startswith("deliver.py plan"))
        import deliver
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.plan(argparse.Namespace(package=str(deliver_pkg), measure=str(deliver_pkg / "measure.json")))
        self.assertTrue((deliver_pkg / "plan.md").exists())
        self.assertIn("stage: 'write'", run.next_step(pkg)["text"], "a debt asks no gate")
        code, rec = self.spine.conclude(deliver_pkg, PROVING_TEST)
        self.assertEqual(rec["state"], "delivered")
        # the real spine writes measure.json and written.json into the package itself
        self.assertTrue((deliver_pkg / "measure.json").exists())
        step = run.next_step(pkg)
        self.assertTrue(step["text"].startswith("review.py prepare --repo repo-x --branch "), step)

        review_pkg = self.root / "review"
        args = argparse.Namespace(repo="repo-x", proposal=None, branch=rec["branch"]["name"],
                                  item="ITEM-1", item_file=str(self.root / "item.md"),
                                  proposal_file=None, package=str(review_pkg), advisory=False)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(review.prepare(args), 0)
        run.bind(pkg, "review", str(review_pkg))
        self.assertIn("name: 'task-review'", run.next_step(pkg)["text"])

        (review_pkg / "findings-in.json").write_text("[]", encoding="utf-8")
        args = argparse.Namespace(package=str(review_pkg), findings=str(review_pkg / "findings-in.json"),
                                  scripts_manual=None, conventions=None, coverage=None, advisory=False)
        with contextlib.redirect_stdout(io.StringIO()):
            review.conclude(args)
        self.assertTrue((review_pkg / "verdict.json").exists())
        stop = run.next_step(pkg)
        self.assertEqual(stop["kind"], "stop")
        self.assertIn(stop["class"], ("approved", "intervention"))
        log = (pkg / "orchestration.md").read_text(encoding="utf-8").splitlines()
        self.assertTrue(log[-1].startswith(("stop:", "parada:")), log[-1])


class TestAStoryAndAnEpicAreRouted(unittest.TestCase):
    """A story is designed, approved, delivered task by task on one branch, judged as
    a whole and reviewed once; an epic is designed and stops. Every step is read from
    what the packages hold."""

    def setUp(self):
        import json, tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "item.md").write_text("# s\n", encoding="utf-8")
        self.d = self.root / "d"; self.d.mkdir()
        self.r = self.root / "r"; self.r.mkdir()
        self.run = {"repo": "repo-x", "item": "S-1", "deliver_package": str(self.d),
                    "review_package": str(self.r), "shape": "story"}
        self.json = json

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, where, name, data):
        where.mkdir(parents=True, exist_ok=True)
        (where / name).write_text(self.json.dumps(data), encoding="utf-8")

    def decide(self):
        from run import decide
        return decide(self.run, self.root)

    def story(self, gate_ok=True):
        self.put(self.d, "deliver-manifest.json", {"shape": "story", "deliverable": True,
                                                   "branch": {"name": "S-1-b"}, "item": {"id": "S-1"}})
        self.put(self.d, "design.json", {"summary": "s", "stories": [], "checked": [],
                                         "tasks": [{"id": "T1", "title": "a", "criteria": ["c"], "files": ["f"],
                                                    "proving_tests": ["t"], "depends_on": []},
                                                   {"id": "T2", "title": "b", "criteria": ["c"], "files": ["g"],
                                                    "proving_tests": ["u"], "depends_on": ["T1"]}]})
        self.put(self.d, "plan.json", {"gate": True, "digest": "d1"})
        if gate_ok:
            self.put(self.d, "approval.json", {"by": "x", "digest": "d1"})

    def test_a_story_is_designed_then_planned_then_approved(self):
        self.put(self.d, "deliver-manifest.json", {"shape": "story", "deliverable": True})
        self.assertIn("stage: 'design'", self.decide()["text"])
        self.put(self.d, "design.json", {"summary": "s", "tasks": [], "stories": [], "checked": []})
        self.assertTrue(self.decide()["text"].startswith("deliver.py plan") and "--design" in self.decide()["text"])
        self.story(gate_ok=False)
        self.assertEqual(self.decide()["class"], "plan")

    def test_the_tasks_are_prepared_in_the_design_s_order_under_the_story(self):
        self.story()
        out = self.decide()["text"]
        self.assertTrue(out.startswith(f"deliver.py prepare --repo repo-x --story {self.d} --task T1"), out)
        self.assertIn(f"--package {self.d / 'tasks' / 'T1'}", out)
        self.put(self.d / "tasks" / "T1", "deliver-manifest.json", {"shape": "bug", "item": {"id": "S-1-T1"}})
        self.put(self.d / "tasks" / "T1", "delivery.json", {"state": "delivered", "branch": {"name": "S-1-b"}})
        self.assertIn("--task T2", self.decide()["text"])

    def test_a_task_in_progress_is_walked_like_any_delivery(self):
        self.story()
        self.put(self.d / "tasks" / "T1", "deliver-manifest.json", {"shape": "bug"})
        self.assertIn("stage: 'measure'", self.decide()["text"])
        self.put(self.d / "tasks" / "T1", "measure.json", {"premises": []})
        self.put(self.d / "tasks" / "T1", "plan.json", {"gate": False, "digest": "x"})
        self.assertIn("stage: 'write'", self.decide()["text"])

    def test_a_blocked_task_stops_the_story_naming_it(self):
        self.story()
        self.put(self.d / "tasks" / "T1", "deliver-manifest.json", {"shape": "bug"})
        self.put(self.d / "tasks" / "T1", "delivery.json", {"state": "blocked", "reason": "new red",
                                                             "branch": {"name": "S-1-b"}})
        out = self.decide()
        self.assertEqual((out["kind"], out["class"]), ("stop", "blocked"))
        self.assertIn("T1", out["text"])

    def test_every_task_delivered_asks_for_the_story_s_record_then_the_review(self):
        self.story()
        for t_ in ("T1", "T2"):
            self.put(self.d / "tasks" / t_, "deliver-manifest.json", {"shape": "bug"})
            self.put(self.d / "tasks" / t_, "delivery.json", {"state": "delivered", "branch": {"name": "S-1-b"}})
        self.assertTrue(self.decide()["text"].startswith(f"deliver.py story --package {self.d}"))
        self.put(self.d, "delivery.json", {"state": "delivered", "branch": {"name": "S-1-b"},
                                           "tasks": {"T1": "delivered", "T2": "delivered"}})
        self.assertTrue(self.decide()["text"].startswith("review.py prepare --repo repo-x --branch S-1-b --item S-1"))
        self.put(self.r, "manifest.json", {})
        self.put(self.r, "verdict.json", {"verdict": "APPROVED"})
        self.assertEqual(self.decide()["class"], "approved")

    def test_an_epic_is_decomposed_and_stops(self):
        self.run["shape"] = "epic"
        self.put(self.d, "deliver-manifest.json", {"shape": "epic", "deliverable": False})
        self.assertIn("stage: 'design'", self.decide()["text"])
        self.put(self.d, "design.json", {"summary": "e", "tasks": [], "checked": [],
                                         "stories": [{"title": "s1", "criteria": ["c"], "out_of_scope": "x"}]})
        self.put(self.d, "plan.json", {"gate": True, "digest": "d1"})
        self.assertEqual(self.decide()["class"], "plan")
        self.put(self.d, "approval.json", {"by": "x", "digest": "d1"})
        out = self.decide()
        self.assertEqual((out["kind"], out["class"]), ("stop", "decomposed"))
        self.assertIn("plan.md", out["text"])


class TestTheReviewTakesTheDeliverysRecord(unittest.TestCase):
    """The branch the prumo delivered carries its proof; a review that does not read it
    asks a person for what the run already measured."""

    def test_the_route_hands_the_delivery_package_to_the_review(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp); (root / "item.md").write_text("# x")
            d = root / "d"; d.mkdir()
            (d / "deliver-manifest.json").write_text("{}")
            (d / "delivery.json").write_text(json.dumps({"state": "delivered", "branch": {"name": "b"}}))
            from run import decide
            out = decide({"repo": "r", "item": "I", "deliver_package": str(d), "review_package": str(root / "r")}, root)
            self.assertIn(f"--delivery {d}", out["text"])

    def test_a_preflight_that_wrote_nothing_is_a_stop_in_words(self):
        import tempfile
        import common
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(common.UsageError) as ctx:
                common.capabilities_after_preflight(pathlib.Path(tmp))
            self.assertIn("capabilities.json", str(ctx.exception))

    def test_the_report_directory_does_not_repeat_the_item_the_origin_carries(self):
        import review
        self.assertEqual(review.report_dir_name("50557", "50557-debito-tecnico-criar-testes"),
                         "50557-debito-tecnico-criar-testes")
        self.assertEqual(review.report_dir_name("50557", "feature/uuid-guard"), "50557-feature-uuid-guard")
        self.assertEqual(review.report_dir_name("50557", "505570-other"), "50557-505570-other")
        self.assertEqual(review.report_dir_name(None, "x"), "no-item-x")
        # render prints the destination and conclude files the report: one spelling.
        # The render line once said `50557-50557-…` while the report landed elsewhere.
        import render
        printed = render.report_path({"repo": "r", "item": {"id": "50557"},
                                      "origin": "50557-debito-tecnico-criar-testes"})
        self.assertTrue(printed.endswith("/r/50557-debito-tecnico-criar-testes/review-" + __import__("common").today() + ".md"),
                        printed)
        self.assertNotIn("50557-50557", printed)

    def test_conclude_absorbs_the_workflow_s_whole_result_and_the_extractor_s_list(self):
        """Five files were written by hand between the workflow and `conclude`, on every
        review; and the extractor ran — or was forgotten — in the harness's memory. The
        workflow now extracts first and returns everything in one object."""
        import json, tempfile
        import review
        result = {"findings": [{"id": "COR-1"}], "coverage": {"checked": ["x"], "reviewers_without_return": 0},
                  "conventions_observed": [], "scripts_manual": [],
                  "criteria": [{"id": "AC-1", "state": "met", "record": "line 37"}],
                  "criteria_extracted": {"criteria": [{"id": "AC-1", "text": "five tests",
                                                       "state": "not_verifiable", "record": None}],
                                         "out_of_scope": ["other middlewares"], "note": None}}
        with tempfile.TemporaryDirectory() as tmp:
            pkg = pathlib.Path(tmp)
            readings = review.absorb_workflow_result(pkg, result)
            for name in ("findings.json", "coverage.json", "conventions_observed.json",
                         "scripts_manual.json", "criteria.json", "out-of-scope.json"):
                self.assertTrue((pkg / name).is_file(), name)
            self.assertEqual(json.loads((pkg / "criteria.json").read_text())[0]["id"], "AC-1")
            self.assertEqual(json.loads((pkg / "out-of-scope.json").read_text()),
                             {"out_of_scope": ["other middlewares"]})
            self.assertEqual(readings, result["criteria"])
            # a list already in the package — confirmed by a person — is never overwritten
            (pkg / "criteria.json").write_text(json.dumps([{"id": "AC-1", "text": "theirs",
                                                            "state": "met", "record": "qa"}]))
            review.absorb_workflow_result(pkg, result)
            self.assertEqual(json.loads((pkg / "criteria.json").read_text())[0]["text"], "theirs")
        workflow = (PRUMO_ROOT / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("phase('Criteria')", workflow)
        self.assertIn("criteria_extracted: extracted", workflow)
        self.assertIn("VERBATIM", workflow)
        self.assertLess(workflow.index("phase('Criteria')"), workflow.index("phase('Review')"))

    def test_a_reviewer_s_reason_is_a_note_and_an_unknown_id_is_reported(self):
        """Four criteria once passed gate 3 on "a person has to confirm them" sitting in
        `record`; and a whole reading once fell on the floor because the reviewers
        numbered the criteria their own way. Neither happens in silence now."""
        import review
        current = [{"id": "AC-1", "text": "five tests", "state": "not_verifiable", "record": None},
                   {"id": "AC-2", "text": "mutants", "state": "not_verifiable", "record": None},
                   {"id": "AC-3", "text": "lint", "state": "not_verifiable", "record": "qa, 2026-11-10"}]
        merged, unmatched = review.merge_criteria(current, [
            {"id": "AC-1", "state": "met", "record": "lines 37, 47, 58, 69 and 81"},
            {"id": "AC-2", "state": "not_verifiable", "record": "a person has to confirm them"},
            {"id": "AC-3", "state": "not_verifiable", "record": "pint not shown"},
            {"id": "c4-format", "state": "met", "record": "ok"},
            {"id": "AC-9", "state": "met", "record": None}])
        self.assertEqual((merged[0]["state"], merged[0]["record"]), ("met", "lines 37, 47, 58, 69 and 81"))
        self.assertEqual((merged[1]["state"], merged[1]["record"], merged[1]["note"]),
                         ("not_verifiable", None, "a person has to confirm them"))
        # a person's record already there is never overwritten by a reviewer's reason
        self.assertEqual((merged[2]["record"], merged[2]["note"]), ("qa, 2026-11-10", "pint not shown"))
        self.assertEqual(unmatched, ["AC-9", "c4-format"])
        import judge
        v = judge.judge_delivery({"status": "confirmed", "anchors": {}}, [], [], merged,
                                 {"suite": "green"}, {}, {})
        self.assertEqual(v["verdict"], "INTERVENTION_REQUIRED")
        self.assertIn("AC-2", v["gates"][2]["items"])
        self.assertNotIn("AC-3", v["gates"][2]["items"])

    def test_the_runs_the_delivery_captured_travel_with_its_record(self):
        """A verifier once confirmed "the record contradicts itself on whether the
        mutants ran" while the mutant's run sat one directory away. The runs now come
        along, under a directory of their own, and the invocation names them."""
        import json, tempfile
        import review
        with tempfile.TemporaryDirectory() as tmp:
            source, pkg = pathlib.Path(tmp) / "deliver", pathlib.Path(tmp) / "review"
            source.mkdir(); pkg.mkdir()
            (source / "delivery.json").write_text(json.dumps({"branch": {"sha": "abc"}}))
            for name in ("run-suite-base.txt", "run-suite-mutation-1.txt", "run-lint.txt"):
                (source / name).write_text(name)
            (source / "plan.md").write_text("not a run")
            runs = review.copy_delivery(source, pkg)
            self.assertEqual(runs, ["run-lint.txt", "run-suite-base.txt", "run-suite-mutation-1.txt"])
            self.assertTrue((pkg / "delivery-record.json").is_file())
            self.assertEqual((pkg / review.RUNS_DIR / "run-suite-mutation-1.txt").read_text(),
                             "run-suite-mutation-1.txt")
            self.assertFalse((pkg / review.RUNS_DIR / "plan.md").exists())
            # a delivery that captured nothing leaves no empty directory behind
            bare = pathlib.Path(tmp) / "bare"; bare.mkdir()
            (bare / "delivery.json").write_text("{}")
            (pkg / "x").mkdir()
            self.assertEqual(review.copy_delivery(bare, pkg / "x"), [])
            self.assertFalse((pkg / "x" / review.RUNS_DIR).exists())
        workflow = (PRUMO_ROOT / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("delivery-runs/", workflow)

    def test_the_runs_are_digested_by_the_profiles_patterns_and_travel_inline(self):
        """The runs travelled since 2.8.0 and the prompt said "open one when a state needs
        checking". On the next real review neither reviewer opened one: both raised, from
        the record's prose, that a mutant had turned ONE test red, while its run held
        seventeen beyond the base. The harness now reads every run once with the
        profile's patterns and hands the reviewers the reds by name — `beyond_base` is
        what a mutant killed — inline, beside the record."""
        import json, tempfile
        import review
        profile = {"checks": {"suite": {"red_name_pattern": r"(?m)^\d+\) (\S+)",
                                        "progress_pattern": r"(\d+) / (\d+) \(\s*\d+%\)"},
                              "lint": {"failure_pattern": r"(?m)^\s*(FAIL|Linter failed)"}}}
        with tempfile.TemporaryDirectory() as tmp:
            pkg = pathlib.Path(tmp); runs = pkg / review.RUNS_DIR; runs.mkdir()
            (runs / "run-suite-base.txt").write_text("1) A::old_red\n 10 / 10 (100%)\nERRORS!\n")
            (runs / "run-suite-branch.txt").write_text("1) A::old_red\n 11 / 11 (100%)\n")
            (runs / "run-suite-mutation-1.txt").write_text(
                "1) A::old_red\n2) B::it_refuses\n3) C::it_refuses_both\n 11 / 11 (100%)\nFAILURES!\n")
            (runs / "run-lint.txt").write_text("  PASS  ........ 1298 files\n")
            digest = review.digest_runs(pkg, profile)
            self.assertEqual(digest["base_run"], "run-suite-base.txt")
            m = digest["runs"]["run-suite-mutation-1.txt"]
            self.assertEqual(m["beyond_base"], ["B::it_refuses", "C::it_refuses_both"])
            self.assertEqual(m["back_to_green"], [])
            self.assertEqual((m["executed"], m["predicted"], m["red_count"]), (11, 11, 3))
            # the base names are listed once, on the base run; the others say what changed
            self.assertEqual(digest["runs"]["run-suite-base.txt"]["reds"], ["A::old_red"])
            self.assertNotIn("reds", m)
            self.assertEqual(digest["runs"]["run-suite-branch.txt"]["beyond_base"], [])
            self.assertEqual(digest["runs"]["run-lint.txt"], {"role": "lint", "failed": False})
            # the digest is a package file the reviewers get inline, never a pointer
            self.assertIn(review.DIGEST_FILE, review.INLINE_FILES)
            self.assertIn(review.DIGEST_FILE, review.inline_payload(pkg))
            self.assertEqual(json.loads((pkg / review.DIGEST_FILE).read_text())["runs"].keys(),
                             digest["runs"].keys())
            # no pattern: no names, and no claim that nothing is red
            bare = review.digest_runs(pkg, {"checks": {}})
            self.assertIsNone(bare["runs"]["run-suite-base.txt"]["reds"])
            self.assertIsNone(bare["runs"]["run-suite-mutation-1.txt"]["beyond_base"])
            self.assertIsNone(bare["runs"]["run-suite-mutation-1.txt"]["red_count"])
            self.assertIsNone(bare["runs"]["run-lint.txt"]["failed"])
            # a package without runs digests nothing and writes nothing
            empty = pathlib.Path(tmp) / "empty"; empty.mkdir()
            self.assertIsNone(review.digest_runs(empty, profile))
            self.assertFalse((empty / review.DIGEST_FILE).exists())
        workflow = (PRUMO_ROOT / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("embed('delivery-digest.json'", workflow)
        self.assertIn("beyond_base", workflow)

    def test_a_finding_nobody_verified_is_unverified_never_confirmed(self):
        """Two nits raised from the record's prose went out as `confirmed` on a review
        whose verifier never saw them — nits do not go to the verifier — while the run
        file refuted both. A status is what happened: `unverified`."""
        import json
        workflow = (PRUMO_ROOT / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("status: 'unverified'", workflow)
        self.assertNotIn("{ ...f, status: 'confirmed'", workflow)
        self.assertIn("NOT_SENT_BELOW_BLOCKING", workflow)
        schema = json.loads((PRUMO_ROOT / "schemas" / "finding.schema.json").read_text(encoding="utf-8"))
        self.assertIn("unverified", json.dumps(schema))

    def test_conclude_reads_the_workflows_object_out_of_the_harness_envelope(self):
        """The task's output file wraps the object in `{summary, logs, result, …}`. The
        first real run of 2.8.0 handed that file to `conclude` and nothing was split."""
        import json, tempfile
        import review
        inner = {"findings": [], "coverage": {"checked": []}, "criteria": [{"id": "AC-1", "state": "met", "record": "r"}]}
        with tempfile.TemporaryDirectory() as tmp:
            env = pathlib.Path(tmp) / "task.output"
            env.write_text(json.dumps({"summary": "x", "logs": ["a"], "result": inner, "totalTokens": 1}))
            bare = pathlib.Path(tmp) / "bare.json"
            bare.write_text(json.dumps(inner))
            self.assertEqual(review.workflow_result_from(env), inner)
            self.assertEqual(review.workflow_result_from(bare), inner)
            # an object that carries the workflow's keys AND a `result` key is itself
            odd = pathlib.Path(tmp) / "odd.json"
            odd.write_text(json.dumps({"findings": [], "result": {"findings": [{"id": "X"}]}}))
            self.assertEqual(review.workflow_result_from(odd)["findings"], [])

    def test_a_slice_that_fits_travels_inline_and_the_reviewer_is_told_not_to_read_it(self):
        """Each reviewer of an 85-line delivery read its slice twice, from the path the
        prompt gave. A slice under the ceiling is carried like any other package file."""
        import json, tempfile
        import review
        with tempfile.TemporaryDirectory() as tmp:
            pkg = pathlib.Path(tmp); (pkg / "slices").mkdir()
            (pkg / "slices" / "complete.patch").write_text("--- a\n+++ b\n+line\n")
            (pkg / "slices" / "huge.patch").write_text("x" * (review.INLINE_CEILING + 1))
            (pkg / "slices" / "unused.patch").write_text("+nobody reads this\n")
            (pkg / "manifest.json").write_text("{}")
            (pkg / "plan.json").write_text(json.dumps({"reviewers": [
                {"agent": "a", "slice": "complete"}, {"agent": "b", "slice": "huge"}]}))
            payload = review.inline_payload(pkg)
            self.assertEqual(payload["slices/complete.patch"], "--- a\n+++ b\n+line\n")
            self.assertNotIn("slices/huge.patch", payload)
            # a slice no reviewer of the plan reads does not travel
            self.assertNotIn("slices/unused.patch", payload)
            self.assertIn("manifest.json", payload)
        workflow = (PRUMO_ROOT / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("inlined[`slices/${slice}.patch`]", workflow)
        self.assertIn("do not read the file", workflow)

    def test_the_harness_names_the_candidate_lines_and_a_summary_is_its_parts(self):
        """The extractor cut the same item into nine criteria on one run and five on the
        next: "the five tests above" as the five, or as one. The harness now names the
        list lines; the extractor labels; and a summary is replaced by its parts
        whatever the parts were labelled — the same labels give the same list."""
        import review
        item = ("# Title\n\n**Objetivo:** do X\n- first test\n- second test\n\n"
                "**Contexto**\nprose\n**Critérios de aceite**\n- Os dois testes acima passando.\n"
                "- Suíte verde.\n- **Fora de escopo:** outros middlewares.\n**Sugestão**\n1. try this\n")
        cands = review.criteria_candidates(item)
        self.assertEqual([c["id"] for c in cands], ["L-1", "L-2", "L-3", "L-4", "L-5", "L-6"])
        self.assertEqual(cands[2]["under"], "Critérios de aceite")
        self.assertEqual(cands[5]["text"], "try this")
        self.assertEqual(cands[0]["under"], "Title")
        labels_a = [{"id": "L-1", "role": "criterion"}, {"id": "L-2", "role": "criterion"},
                    {"id": "L-3", "role": "summary", "of": ["L-1", "L-2"]},
                    {"id": "L-4", "role": "criterion"}, {"id": "L-5", "role": "out_of_scope"},
                    {"id": "L-6", "role": "context"}]
        labels_b = [{"id": "L-1", "role": "context"}, {"id": "L-2", "role": "context"},
                    {"id": "L-3", "role": "summary", "of": ["L-1", "L-2"]},
                    {"id": "L-4", "role": "criterion"}, {"id": "L-5", "role": "out_of_scope"}]
        crit_a, out_a = review.build_criteria(cands, labels_a)
        crit_b, out_b = review.build_criteria(cands, labels_b)
        self.assertEqual([c["text"] for c in crit_a], ["first test", "second test", "Suíte verde."])
        self.assertEqual(crit_a, crit_b)
        self.assertEqual([c["id"] for c in crit_a], ["AC-1", "AC-2", "AC-3"])
        self.assertEqual(out_a, ["**Fora de escopo:** outros middlewares."])
        # a summary of nothing known is itself the criterion; an unknown id is ignored
        crit_c, _ = review.build_criteria(cands, [{"id": "L-3", "role": "summary", "of": ["L-99"]},
                                                  {"id": "L-77", "role": "criterion"}])
        self.assertEqual([c["candidate"] for c in crit_c], ["L-3"])
        self.assertIn(review.CANDIDATES_FILE, review.INLINE_FILES)
        workflow = (PRUMO_ROOT / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("criteria-candidates.json", workflow)
        self.assertIn("criteria_labels: labels", workflow)

    def test_conclude_builds_the_criteria_from_the_labels_not_from_the_extractors_list(self):
        import json, tempfile
        import review
        with tempfile.TemporaryDirectory() as tmp:
            pkg = pathlib.Path(tmp)
            (pkg / review.CANDIDATES_FILE).write_text(json.dumps(
                review.criteria_candidates("- a\n- b\n- a and b above\n")))
            readings = review.absorb_workflow_result(pkg, {
                "criteria_labels": [{"id": "L-1", "role": "criterion"}, {"id": "L-2", "role": "criterion"},
                                    {"id": "L-3", "role": "summary", "of": ["L-1", "L-2"]}],
                "criteria_extracted": {"criteria": [{"id": "AC-1", "text": "wrong"}], "out_of_scope": []},
                "criteria": [{"id": "AC-2", "state": "met", "record": "r"}]})
            self.assertEqual([c["text"] for c in json.loads((pkg / "criteria.json").read_text())], ["a", "b"])
            self.assertEqual(readings, [{"id": "AC-2", "state": "met", "record": "r"}])

    def test_the_suite_the_delivery_measured_on_this_head_is_evidence(self):
        import json, tempfile
        import review
        with tempfile.TemporaryDirectory() as tmp:
            pkg = pathlib.Path(tmp)
            (pkg / "manifest.json").write_text(json.dumps({"head_sha": "abc", "behind_base": False}))
            (pkg / "capabilities.json").write_text(json.dumps({"checks": {"suite": {"available": True}}}))
            (pkg / "delivery-record.json").write_text(json.dumps({
                "branch": {"sha": "abc"}, "lint": {"state": "green", "reason": "ok"},
                "baseline": {"base_reds": ["t_old"], "branch_reds": ["t_old"], "new_reds": [],
                             "vanished_reds": [], "predicted": 10, "executed": 10}}))
            ev = review.initial_evidence(pkg)
            self.assertEqual(ev["suite"], "green")
            self.assertEqual(ev["checks"]["lint"]["state"], "green")
            # another head: the record is somebody else's proof
            (pkg / "manifest.json").write_text(json.dumps({"head_sha": "zzz", "behind_base": False}))
            self.assertEqual(review.initial_evidence(pkg)["suite"], "not_run")
            # a new red by name on this head holds the proposal
            (pkg / "manifest.json").write_text(json.dumps({"head_sha": "abc", "behind_base": False}))
            rec = json.loads((pkg / "delivery-record.json").read_text()); rec["baseline"]["new_reds"] = ["t_new"]
            (pkg / "delivery-record.json").write_text(json.dumps(rec))
            self.assertEqual(review.initial_evidence(pkg)["suite"], "red_attributable")


class TestTheVerifierReadsTheSeverityASecondTime(unittest.TestCase):
    def test_the_workflow_asks_for_and_carries_the_consequence_kind(self):
        workflow = (PRUMO_ROOT / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("consequence_kind: { enum: ['behaviour', 'proof', 'record', 'convention'] }", workflow)
        self.assertIn("'attributable', 'consequence_kind']", workflow)
        self.assertIn("consequence_kind: v ? v.consequence_kind : null", workflow)
        self.assertIn("in `consequence_kind`", workflow)

    def test_the_verifier_s_prompt_names_the_four_kinds(self):
        agent = (PRUMO_ROOT / "agents" / "finding-verifier.md").read_text(encoding="utf-8")
        for kind in ("`behaviour`", "`proof`", "`record`", "`convention`"):
            self.assertIn(kind, agent)
        self.assertIn("- `consequence_kind`:", agent)


class TestEveryAgentIsToldHowToReturn(unittest.TestCase):
    """40 of 263 structured returns failed over 42 runs and were regenerated at output
    price; 29 began at `"checked": [` with unescaped quotes and backslashes, the rest
    wrapped the JSON as a string under `input`. The rule travels with every prompt."""

    def test_the_rule_reaches_reviewers_extractor_verifier_and_planner(self):
        workflow = (PRUMO_ROOT / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("const RETURN_RULE", workflow)
        self.assertEqual(workflow.count("RETURN_RULE,"), 5)
        self.assertIn("never wrapped under `input`", workflow)

    def test_no_reviewer_still_asks_for_the_single_finding_schema(self):
        for agent in (PRUMO_ROOT / "agents").glob("*-reviewer.md"):
            text = agent.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"only\*\* with the JSON of the `finding\.schema\.json` schema", agent.name)
            self.assertIn("never a string", text, agent.name)
