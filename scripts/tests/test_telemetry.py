"""Telemetry is the ground under every cost decision — if it lies, the next cut is blind.

What these tests hold: absence does not become zero, instances of the same agent add up,
the two totals stay apart, and the streaming chunks of one response count as the single
request they are.
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import telemetry
from i18n import t


def _session(tmp_path, run_id="wf_test", agents=(), package="/tmp/pkg-x", total=99):
    """Builds on disk the shape the harness writes: projects/<proj>/<session>/..."""
    session = tmp_path / "projects" / "-workspaces" / "sess1"
    (session / "workflows").mkdir(parents=True)
    (session / "workflows" / f"{run_id}.json").write_text(json.dumps({
        "runId": run_id, "timestamp": "2026-10-29T13:00:00.000Z",
        "script": f"const package = '{package}'", "totalTokens": total,
        "durationMs": 120000, "agentCount": len(agents), "totalToolCalls": 7,
        "defaultModel": "claude-opus-5", "status": "completed",
    }), encoding="utf-8")

    agents_dir = session / "subagents" / "workflows" / run_id
    agents_dir.mkdir(parents=True)
    for i, (kind, usages) in enumerate(agents):
        name = f"agent-{i}"
        (agents_dir / f"{name}.meta.json").write_text(
            json.dumps({"agentType": kind, "spawnDepth": 1}), encoding="utf-8")
        lines = [json.dumps({"type": "assistant",
                             "message": {"model": "claude-opus-5", "usage": u}})
                 for u in usages]
        (agents_dir / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return session


class TestTelemetry(unittest.TestCase):
    """This was the only class in the suite written in pytest, which made it vanish from
    `unittest discover` on any machine without pytest installed — a gap that announced
    itself nowhere. Converted to unittest on 2026-11-03, by the same principle that
    governs the rest of the method: whatever could not be assessed has to declare itself,
    and a module that never even loads declares nothing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._tmp.cleanup)


    def test_with_no_record_the_cost_comes_out_null_and_not_zero(self):
        """A zero would make an expensive review look cheap. Absence has to declare itself."""
        m = telemetry.measure("/tmp/does-not-exist", None)
        self.assertIsNone(m["tokens"])
        self.assertIsNone(m["minutes"])
        self.assertEqual(m["reason"], t("telemetry.no_record",
                                        target="/tmp/does-not-exist",
                                        root=telemetry.projects_root()))


    def test_it_adds_the_four_components_of_every_request(self):
        _session(self.root, agents=[("architecture-reviewer", [
            {"input_tokens": 1, "output_tokens": 10,
             "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000},
            {"input_tokens": 2, "output_tokens": 20,
             "cache_creation_input_tokens": 200, "cache_read_input_tokens": 2000},
        ])])
        m = telemetry.measure("/tmp/pkg-x", None)
        self.assertEqual(m["tokens"], 3333)
        self.assertEqual(m["components"]["cache_read_input_tokens"], 3000)
        self.assertEqual(m["per_agent"][0]["messages"], 2)


    def test_instances_of_the_same_agent_add_up_instead_of_collapsing(self):
        """A workflow opens one `finding-verifier` per blocking finding. Keeping only the
        last would hide precisely the axis that grows most on a bad delivery."""
        usage = [{"input_tokens": 0, "output_tokens": 5,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}]
        _session(self.root, agents=[("finding-verifier", usage)] * 4)
        m = telemetry.measure("/tmp/pkg-x", None)
        self.assertEqual(m["cost_per_agent"]["finding-verifier"]["instances"], 4)
        self.assertEqual(m["cost_per_agent"]["finding-verifier"]["billable"], 20)


    def test_the_two_totals_stay_apart(self):
        """The harness's `totalTokens` and the sum of the components really do diverge
        (835k against 10.8M on the first measurement). Collapsing them into one field
        would hide the divergence."""
        _session(self.root, total=42, agents=[("domain-rules-reviewer", [
            {"input_tokens": 0, "output_tokens": 7,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}])])
        m = telemetry.measure("/tmp/pkg-x", None)
        self.assertEqual(m["harness_tokens"], 42)
        self.assertEqual(m["billable_tokens"], 7)


    def test_the_chunks_of_one_response_count_as_one_request(self):
        """The transcript writes a line per streaming chunk, and every chunk repeats the
        same `usage` snapshot. Summing line by line counted each request two to five
        times: one agent measured on 2026-11-04 had 46 lines carrying `usage` and 17
        distinct ids, and the eleven reviews on disk added up to 112M against a real 48M.

        This is the test that stops an inflated figure, which is worse than no figure —
        it is what every optimisation is judged against, and it was about to be used to
        cut a phase that was not the expensive one."""
        session = _session(self.root, agents=[("correctness-proof-reviewer", [])])
        agents_dir = session / "subagents" / "workflows" / "wf_test"
        chunk = {"input_tokens": 2, "output_tokens": 8,
                 "cache_creation_input_tokens": 1878, "cache_read_input_tokens": 56812}
        final = dict(chunk, output_tokens=448)
        lines = [json.dumps({"type": "assistant",
                             "message": {"id": "msg_A", "model": "claude-opus-5",
                                         "usage": u}})
                 # four partial chunks and the complete one, all of a single response
                 for u in (chunk, chunk, chunk, chunk, final)]
        lines.append(json.dumps({"type": "assistant",
                                 "message": {"id": "msg_B", "model": "claude-opus-5",
                                             "usage": chunk}}))
        (agents_dir / "agent-0.jsonl").write_text("\n".join(lines) + "\n",
                                                  encoding="utf-8")

        m = telemetry.measure("/tmp/pkg-x", None)
        self.assertEqual(m["per_agent"][0]["messages"], 2)
        # the LAST snapshot of an id wins: only the final chunk carries the whole output
        self.assertEqual(m["tokens"], sum(final.values()) + sum(chunk.values()))


    def test_a_response_with_no_id_is_still_counted_once(self):
        """An uncountable request is still a request: with nothing to collapse it with,
        it gets a key of its own rather than being dropped."""
        _session(self.root, agents=[("security-privacy-reviewer", [
            {"input_tokens": 1, "output_tokens": 1,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            {"input_tokens": 1, "output_tokens": 1,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        ])])
        m = telemetry.measure("/tmp/pkg-x", None)
        self.assertEqual(m["per_agent"][0]["messages"], 2)
        self.assertEqual(m["tokens"], 4)


    def test_the_most_recent_run_wins_when_there_is_a_repeat(self):
        """A review repeated after a failure leaves two records over the same package."""
        session = _session(self.root, run_id="wf_old", agents=[("x", [])])
        (session / "workflows" / "wf_new.json").write_text(json.dumps({
            "runId": "wf_new", "timestamp": "2026-10-29T18:00:00.000Z",
            "script": "const package = '/tmp/pkg-x'", "totalTokens": 1,
            "durationMs": 1000, "status": "completed",
        }), encoding="utf-8")
        self.assertEqual(telemetry.measure("/tmp/pkg-x", None)["run_id"], "wf_new")


if __name__ == "__main__":
    unittest.main(verbosity=2)
