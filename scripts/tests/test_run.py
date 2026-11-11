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
        self.assertIn("name: 'task-review'", self.decide()["text"])
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

    def test_an_intervention_with_an_author_fix_route_hands_the_fix_door(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "b"}})
        self.put(self.review, "manifest.json", {})
        self.put(self.review, "verdict.json", {"verdict": "INTERVENTION_REQUIRED", "routes": [
            {"type": "author_fix", "items": ["COR-1"], "owner": "author", "closes_when": "x"}]})
        out = self.decide()
        self.assertEqual(out["class"], "intervention")
        self.assertIn(f"deliver.py prepare --repo repo-x --fix {self.review}", out["text"])

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
