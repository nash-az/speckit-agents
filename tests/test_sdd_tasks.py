import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE.parent / "bin" / "sdd-tasks.py"
spec = importlib.util.spec_from_file_location("sdd_tasks", SCRIPT)
sdd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdd)

TASKS = HERE / "fixtures" / "tasks.md"
BLOCKED = HERE / "fixtures" / "BLOCKED.md"


def tasks_by_id(parsed):
    return {t["id"]: t for p in parsed["phases"] for t in p["tasks"]}


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.parsed = sdd.parse_tasks(TASKS)
        self.tasks = tasks_by_id(self.parsed)

    def test_phases_and_ids(self):
        self.assertEqual([p["id"] for p in self.parsed["phases"]], ["1", "2", "3", "3a"])
        self.assertNotIn("T999", self.tasks)  # after the last phase

    def test_flags_tags_and_files(self):
        t = self.tasks["T009"]
        self.assertTrue(t["parallel"])
        self.assertEqual(t["story"], "US1")
        self.assertEqual(t["tags"], ["eval"])
        self.assertEqual(t["files"], ["src/eval/acc.py"])  # `svx eval accuracy` is not a path
        self.assertEqual(self.tasks["T008"]["tags"], ["manual"])
        self.assertTrue(self.tasks["T001"]["done"])

    def test_comma_tags_and_arrow_paths(self):
        t = self.tasks["T012"]
        self.assertTrue(t["parallel"])
        self.assertEqual(t["story"], "US2")
        self.assertEqual(t["files"], ["src/x.py", "src/y.py"])

    def test_blocked(self):
        self.assertEqual(sdd.parse_blocked(BLOCKED), {"T006": "open", "T003": "resolved"})

    def test_parse_command_output_unchanged(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "parse", str(TASKS), "--blocked", str(BLOCKED)],
                             capture_output=True, text=True, check=True)
        result = json.loads(out.stdout)
        self.assertEqual(result["blocked"], {"T006": "open", "T003": "resolved"})
        self.assertEqual(result["phases"], json.loads(json.dumps(self.parsed["phases"])))


class BlockedEntriesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.blocked = self.dir / "BLOCKED.md"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_fixture_fields(self):
        entries = sdd.parse_blocked_entries(BLOCKED)
        self.assertEqual(entries["T006"], {"status": "open", "tag": "decision", "reason": "r",
                                           "question": "q", "answer": ""})
        self.assertEqual(entries["T003"], {"status": "resolved", "tag": "", "reason": "",
                                           "question": "", "answer": "done"})

    def test_last_entry_per_id_wins(self):
        self.blocked.write_text(
            "# Blocked tasks\n\n"
            "## T008\n- status: Resolved\n- tag: manual\n- reason: old\n- question: old q\n- answer: yes\n\n"
            "## T008\n- status: open\n- tag: failed\n- reason: new\n- question: new q\n- answer:\n",
            encoding="utf-8")
        entries = sdd.parse_blocked_entries(self.blocked)
        self.assertEqual(entries["T008"], {"status": "open", "tag": "failed", "reason": "new",
                                           "question": "new q", "answer": ""})
        self.assertEqual(sdd.parse_blocked(self.blocked), {"T008": "open"})

    def test_multi_line_answer(self):
        self.blocked.write_text(
            "## T008\n- status: resolved\n- tag: decision\n- question: Which one?\n- answer:\n"
            "Use the second option.\n\n  Keep the old API as well.\n"
            "- note: not part of the answer\n"
            "## Notes\nnot part of any entry\n",
            encoding="utf-8")
        entry = sdd.parse_blocked_entries(self.blocked)["T008"]
        self.assertEqual(entry["answer"], "Use the second option.\n\n  Keep the old API as well.")
        self.assertEqual(entry["status"], "resolved")
        self.assertEqual(entry["question"], "Which one?")

    def test_status_defaults_to_open(self):
        self.blocked.write_text("## T008\n- tag: manual\n", encoding="utf-8")
        self.assertEqual(sdd.parse_blocked_entries(self.blocked)["T008"]["status"], "open")

    def test_missing_file(self):
        self.assertEqual(sdd.parse_blocked_entries(self.dir / "nope.md"), {})
        self.assertEqual(sdd.parse_blocked_entries(None), {})
        self.assertEqual(sdd.parse_blocked(self.dir / "nope.md"), {})


class PlanTest(unittest.TestCase):
    def plan(self, **allow):
        config = sdd.load_config(None)
        config["allow"].update(allow)
        return sdd.plan(sdd.parse_tasks(TASKS), sdd.parse_blocked(BLOCKED), config, None, None)

    def test_holds(self):
        held = {h["id"]: h["tag"] for h in self.plan()["held"]}
        self.assertEqual(held, {"T006": "blocked", "T008": "manual", "T010": "dep"})

    def test_resolved_is_runnable(self):
        config = sdd.load_config(None)
        result = sdd.plan(sdd.parse_tasks(TASKS), {"T003": "resolved"}, config, None, None)
        self.assertNotIn("T003", {h["id"] for h in result["held"]})
        self.assertIn("T003", {tid for p in result["phases"] for g in p["groups"] for tid in g})

    def test_resolved_answer_reaches_task(self):
        config = sdd.load_config(None)
        entries = {"T003": {"status": "resolved", "tag": "decision", "reason": "r",
                            "question": "Which one?", "answer": "The second."}}
        result = sdd.plan(sdd.parse_tasks(TASKS), entries, config, None, None)
        task = next(p["tasks"]["T003"] for p in result["phases"] if "T003" in p["tasks"])
        self.assertEqual(task["answer"], "The second.")
        self.assertEqual(task["question"], "Which one?")

    def test_answer_needs_resolved_and_non_empty(self):
        config = sdd.load_config(None)
        parsed = sdd.parse_tasks(TASKS)
        entry = {"status": "resolved", "question": "q", "answer": ""}
        result = sdd.plan(parsed, {"T003": entry}, config, None, None)
        task = next(p["tasks"]["T003"] for p in result["phases"] if "T003" in p["tasks"])
        self.assertNotIn("answer", task)
        self.assertNotIn("question", task)
        # Other runnable tasks never get the fields, and the parsed input is not changed.
        result = sdd.plan(parsed, sdd.parse_blocked_entries(BLOCKED), config, None, None)
        others = [t for p in result["phases"] for tid, t in p["tasks"].items() if tid != "T003"]
        self.assertTrue(all("answer" not in t for t in others))
        self.assertTrue(all("answer" not in t for p in parsed["phases"] for t in p["tasks"]))

    def test_plan_command_passes_answers(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "plan", str(TASKS), "--blocked", str(BLOCKED)],
                             capture_output=True, text=True, check=True)
        result = json.loads(out.stdout)
        task = next(p["tasks"]["T003"] for p in result["phases"] if "T003" in p["tasks"])
        self.assertEqual(task["answer"], "done")
        self.assertEqual(task["question"], "")
        self.assertIn("T006", {h["id"] for h in result["held"]})

    def test_moved_is_held(self):
        config = sdd.load_config(None)
        result = sdd.plan(sdd.parse_tasks(TASKS), {"T003": "moved"}, config, None, None)
        self.assertIn({"id": "T003", "tag": "moved", "reason": "moved to product/admin use"}, result["held"])

    def test_open_is_held_as_blocked(self):
        config = sdd.load_config(None)
        result = sdd.plan(sdd.parse_tasks(TASKS), {"T003": "open"}, config, None, None)
        self.assertIn({"id": "T003", "tag": "blocked", "reason": "open entry in BLOCKED.md"}, result["held"])

    def test_wontfix_stays_held(self):
        # An entry dropped with `status: wontfix` must not send its open task back to the agents.
        config = sdd.load_config(None)
        result = sdd.plan(sdd.parse_tasks(TASKS), {"T006": "wontfix"}, config, None, None)
        self.assertIn({"id": "T006", "tag": "wontfix", "reason": "dropped in BLOCKED.md (wontfix)"}, result["held"])

    def test_allow_dependencies(self):
        held = {h["id"] for h in self.plan(dependencies=True)["held"]}
        self.assertNotIn("T010", held)

    def test_dep_ok_approves_one_task(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        tasks = tmp / "tasks.md"
        tasks.write_text(
            "## Phase 1: Setup\n\n"
            "- [ ] T001 [dep] [dep:ok] Add `numpy` in `pyproject.toml`\n"
            "- [ ] T002 [dep] Add `scipy` in `setup.cfg`\n"
            "- [ ] T003 [download] [DOWNLOAD:OK] Fetch the model into `models/a.bin`\n",
            encoding="utf-8")
        parsed = sdd.parse_tasks(tasks)
        config = sdd.load_config(None)
        self.assertFalse(config["allow"]["dependencies"])
        held = {h["id"]: h["tag"] for h in sdd.plan(parsed, {}, config, None, None)["held"]}
        self.assertEqual(held, {"T002": "dep"})
        self.assertEqual(tasks_by_id(parsed)["T003"]["tags"], ["download", "download:ok"])

    def test_upper_case_dep_ok_is_lowercased(self):
        self.assertEqual(sdd.parse_task(sdd.TASK_RE.match("- [ ] T001 [dep] [DEP:OK] Add x"), 1)["tags"],
                         ["dep", "dep:ok"])

    def test_groups_split_on_shared_files_and_non_parallel(self):
        phases = {p["id"]: p["groups"] for p in self.plan()["phases"]}
        self.assertEqual(phases["1"], [["T002", "T003"]])  # T003 is resolved in BLOCKED.md: runnable
        self.assertEqual(phases["2"], [["T004"], ["T005"]])  # T005 also touches a.py
        self.assertEqual(phases["3"], [["T007", "T009"], ["T011"]])

    def test_phase_filter_and_cap(self):
        config = sdd.load_config(None)
        out = sdd.plan(sdd.parse_tasks(TASKS), {}, config, ["3"], 1)
        self.assertEqual([p["id"] for p in out["phases"]], ["3"])
        self.assertEqual(out["runnable"], 1)


class SupersededTest(unittest.TestCase):
    """data-model §3.4: explicit superseded-by, computed after converge, no propagation."""

    def plan(self, text, blocked=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        tasks = tmp / "tasks.md"
        tasks.write_text(text, encoding="utf-8")
        result = sdd.plan(sdd.parse_tasks(tasks), blocked or {}, sdd.load_config(None), None, None)
        held = {h["id"]: (h["tag"], h["reason"]) for h in result["held"]}
        runnable = {tid for p in result["phases"] for g in p["groups"] for tid in g}
        return held, runnable

    def test_explicit_superseded_by_holds_the_task_itself(self):
        held, runnable = self.plan(
            "## Phase 1: Setup\n\n"
            "- [ ] T001 Old work, superseded-by T003 in `a.py`\n"
            "- [ ] T002 Other work, replaced-by T003, T004 in `b.py`\n"
            "- [x] T003 New work in `a.py`\n"
            "- [ ] T004 More new work in `c.py`\n")
        self.assertEqual(held["T001"], ("superseded", "T003"))  # successor done: still superseded
        self.assertEqual(held["T002"][0], "superseded")
        self.assertIn("T003", held["T002"][1])
        self.assertNotIn("T003", held)
        self.assertIn("T004", runnable)

    def test_explicit_overwrites_blocked_hold(self):
        held, _ = self.plan("## Phase 1: Setup\n\n- [ ] T001 Old, superseded-by T002\n- [ ] T002 New\n",
                            {"T001": "open"})
        self.assertEqual(held["T001"], ("superseded", "T002"))

    def test_phases_before_convergence_are_superseded(self):
        held, runnable = self.plan(
            "## Phase 1: Setup\n\n- [ ] T001 Old in `a.py`\n- [x] T002 Done in `b.py`\n\n"
            "## Phase 2: Convergence\n\n- [ ] T010 Carry on in `c.py`\n")
        self.assertEqual(held["T001"], ("superseded", "Phase 2"))
        self.assertNotIn("T002", held)
        self.assertIn("T010", runnable)

    def test_per_dependent_of_superseded_stays_runnable(self):
        held, runnable = self.plan(
            "## Phase 1: Setup\n\n- [ ] T020 Old in `a.py`\n\n"
            "## Phase 2: convergence of the drift\n\n- [ ] T090 Finish per T020 in `a.py`\n")
        self.assertEqual(held["T020"], ("superseded", "Phase 2"))
        self.assertNotIn("T090", held)
        self.assertIn("T090", runnable)

    def test_manual_task_superseded_wins_and_successor_runs(self):
        held, runnable = self.plan(
            "## Phase 1: Setup\n\n- [ ] T020 [manual] Record the audio in `a.wav`\n\n"
            "## Phase 2: Convergence\n\n- [ ] T090 Use the recording per T020 in `a.py`\n")
        self.assertEqual(held["T020"], ("superseded", "Phase 2"))
        self.assertIn("T090", runnable)

    def test_phase_after_convergence_is_untouched(self):
        held, runnable = self.plan(
            "## Phase 1: Convergence\n\n- [ ] T010 Converge in `a.py`\n\n"
            "## Phase 2: Polish\n\n- [ ] T020 Polish in `b.py`\n")
        self.assertEqual(held, {})
        self.assertEqual(runnable, {"T010", "T020"})

    def test_older_convergence_phase_is_untouched(self):
        held, runnable = self.plan(
            "## Phase 1: Setup\n\n- [ ] T001 Old in `a.py`\n\n"
            "## Phase 2: Convergence one\n\n- [ ] T010 First converge in `b.py`\n\n"
            "## Phase 3: Feature\n\n- [ ] T020 Feature in `c.py`\n\n"
            "## Phase 4: CONVERGENCE two\n\n- [ ] T030 Second converge in `d.py`\n")
        self.assertEqual(held, {"T001": ("superseded", "Phase 4"), "T020": ("superseded", "Phase 4")})
        self.assertEqual(runnable, {"T010", "T030"})


class HeldDependencyTest(unittest.TestCase):
    """FR-009, research.md "Dependency of a held task": a dependent of a held task is held, to a fixpoint."""

    def test_after_manual_task_is_held_to_fixpoint(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        tasks = tmp / "tasks.md"
        # T003 is listed before T002, so one pass is not enough: only a fixpoint holds it.
        tasks.write_text(
            "## Phase 1: Setup\n\n"
            "- [ ] T001 [manual] Record the audio in `a.wav`\n"
            "- [ ] T003 Tune the model after T002 in `c.py`\n"
            "- [ ] T002 Use the recording after T001 in `b.py`\n"
            "- [ ] T004 Unrelated work in `d.py`\n",
            encoding="utf-8")
        result = sdd.plan(sdd.parse_tasks(tasks), {}, sdd.load_config(None), None, None)
        held = {h["id"]: h["tag"] for h in result["held"]}
        self.assertEqual(held, {"T001": "manual", "T002": "held", "T003": "held"})
        runnable = {tid for p in result["phases"] for g in p["groups"] for tid in g}
        self.assertEqual(runnable, {"T004"})


class ConvergeAgainTest(unittest.TestCase):
    """FR-010, research.md "Converge blocked by held tasks": a Convergence phase with only held tasks
    plans as `runnable: 0`, with its tasks under `held`.

    skills/sdd-run/SKILL.md step 2.1 reads this to decide to converge again: "Open tasks that are only
    held (tagged for a person, or `wontfix` in BLOCKED.md) don't count: they may stay open for a long
    time, and the spec may have changed since that phase was written, so converge again."
    """

    def test_held_only_convergence_phase_has_nothing_runnable(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        tasks = tmp / "tasks.md"
        blocked = tmp / "BLOCKED.md"
        tasks.write_text(
            "## Phase 1: Setup\n\n- [x] T001 Set up in `a.py`\n\n"
            "## Phase 8: Convergence\n\n- [x] T080 First converge in `b.py`\n\n"
            "## Phase 9: Convergence\n\n"
            "- [ ] T090 [manual] Record the audio in `c.wav`\n"
            "- [ ] T091 Dropped work in `d.py`\n"
            "- [x] T092 Done work in `e.py`\n",
            encoding="utf-8")
        blocked.write_text(
            "# Blocked tasks\n\n## T091\n- status: wontfix\n- tag: decision\n- reason: r\n"
            "- question: q\n- answer: not needed\n",
            encoding="utf-8")
        out = subprocess.run([sys.executable, str(SCRIPT), "plan", str(tasks), "--blocked", str(blocked),
                              "--phases", "9"], capture_output=True, text=True, check=True)
        result = json.loads(out.stdout)
        self.assertEqual(result["runnable"], 0)
        held = {h["id"]: h["tag"] for h in result["held"]}
        self.assertEqual(held, {"T090": "manual", "T091": "wontfix"})
        self.assertEqual([tid for p in result["phases"] for g in p["groups"] for tid in g], [])


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.tasks = self.dir / "tasks.md"
        shutil.copy(TASKS, self.tasks)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_tick_only_named_open_tasks(self):
        out = sdd.tick(self.tasks, ["T002", "T001", "T404"])
        self.assertEqual(out["ticked"], ["T002"])
        self.assertEqual(out["missing"], ["T001", "T404"])
        text = self.tasks.read_text()
        self.assertIn("- [x] T002 [P] Add lint config", text)
        self.assertIn("- [ ] T003", text)

    def test_block_appends_once(self):
        blocked = self.dir / "BLOCKED.md"
        self.assertTrue(sdd.block(blocked, "T008", "manual", "needs a person", "Record it")["appended"])
        self.assertFalse(sdd.block(blocked, "T008", "manual", "again", "again")["appended"])
        self.assertEqual(sdd.parse_blocked(blocked), {"T008": "open"})

    def test_block_writes_new_header(self):
        blocked = self.dir / "BLOCKED.md"
        sdd.block(blocked, "T008", "manual", "needs a person", "Record it")
        self.assertTrue(blocked.read_text(encoding="utf-8").startswith(
            "# Blocked tasks\n\n"
            "Tasks an unattended run could not finish. Answer the question under `- answer:` and set\n"
            "`status: resolved`: the next `/sdd-run` runs the task again with your answer.\n"
            "Set `status: wontfix` to drop the task, or `status: moved` if it became work outside this project.\n"
            "\n## T008\n"))

    def test_cli_prints_json(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "plan", str(self.tasks), "--config", "/nonexistent"],
                             capture_output=True, text=True, check=True)
        self.assertIn("phases", json.loads(out.stdout))


class ConfigErrorsTest(unittest.TestCase):
    """data-model §3.8: one error string per bad entry."""

    def errors(self, **sections):
        config = sdd.load_config(None)
        for section, values in sections.items():
            config.setdefault(section, {}).update(values)
        return sdd.config_errors(config)

    def test_default_config_has_no_errors_and_no_isolation(self):
        self.assertEqual(sdd.config_errors(sdd.load_config(None)), [])
        self.assertNotIn("isolation", sdd.DEFAULT_CONFIG["run"])

    def test_unknown_role(self):
        self.assertEqual(self.errors(agents={"reviewer": "sonnet"}), ["agents.reviewer: unknown role"])

    def test_empty_model(self):
        self.assertEqual(self.errors(agents={"verifier": ""}),
                         ["agents.verifier: model must be a non-empty string"])

    def test_profile_without_model(self):
        self.assertEqual(self.errors(profiles={"local": {"env": {"OLLAMA_HOST": "x"}}}),
                         ["profiles.local.model: missing"])

    def test_non_string_env(self):
        self.assertEqual(self.errors(profiles={"local": {"model": "qwen", "env": {"PORT": 11434}}}),
                         ["profiles.local.env: must map names to strings"])

    def test_secret_like_env_name(self):
        self.assertEqual(self.errors(profiles={"local": {"model": "qwen", "env": {"Api_Key": "x"}}}),
                         ["profiles.local.env.Api_Key: secrets must come from your environment, not the config"])

    def test_worktree_isolation(self):
        self.assertEqual(self.errors(run={"isolation": "worktree"}),
                         ["run.isolation: worktree isolation was removed; delete this line"])

    def test_bedrock_arn_model_passes(self):
        arn = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-sonnet-4-v1:0"
        self.assertEqual(self.errors(agents={"implementer": arn},
                                     profiles={"bedrock": {"model": arn, "env": {"AWS_REGION": "us-east-1"}}}), [])

    def test_cli_config_prints_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[run]\nisolation = "worktree"\n', encoding="utf-8")
            out = subprocess.run([sys.executable, str(SCRIPT), "config", "--config", str(path)],
                                 capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout)["errors"],
                         ["run.isolation: worktree isolation was removed; delete this line"])


class SnapshotTest(unittest.TestCase):
    """data-model §3.6: only edits to text, tags, files, deps or story count as changes."""

    BASE = ("## Phase 1: Setup\n\n"
            "- [ ] T001 [P] Add the parser in `a.py`\n"
            "- [ ] T002 Wire it per T001 in `b.py`\n")

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.tasks = self.dir / "tasks.md"
        self.tasks.write_text(self.BASE, encoding="utf-8")
        self.old = sdd.snapshot(sdd.parse_tasks(self.tasks))

    def changed_after(self, text):
        self.tasks.write_text(text, encoding="utf-8")
        return sdd.changed(sdd.parse_tasks(self.tasks), self.old)

    def test_snapshot_fields(self):
        self.assertEqual(self.old["T002"], {"text": "Wire it per T001 in `b.py`", "tags": [],
                                            "files": ["b.py"], "deps": ["T001"], "story": None})

    def test_edited_description_is_listed(self):
        self.assertEqual(self.changed_after(self.BASE.replace("Add the parser", "Add the lexer")), ["T001"])

    def test_ticked_checkbox_is_not_listed(self):
        self.assertEqual(self.changed_after(self.BASE.replace("- [ ] T001", "- [x] T001")), [])

    def test_moved_task_is_not_listed(self):
        self.assertEqual(self.changed_after("## Phase 1: Setup\n\nSome intro.\n\n"
                                            "- [ ] T002 Wire it per T001 in `b.py`\n"
                                            "- [ ] T001 [P] Add the parser in `a.py`\n"), [])

    def test_cli_snapshot_and_changed(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "snapshot", str(self.tasks)],
                             capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout), self.old)
        run_json = self.dir / "run.json"
        run_json.write_text(json.dumps({"task_snapshot": self.old}), encoding="utf-8")
        self.tasks.write_text(self.BASE.replace("`b.py`", "`c.py`"), encoding="utf-8")
        cmd = [sys.executable, str(SCRIPT), "changed", str(self.tasks), "--run-json", str(run_json)]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout), {"changed": ["T002"]})
        run_json.write_text(json.dumps({"run_id": "x"}), encoding="utf-8")
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout), {"changed": []})


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.cp = sdd.checkpoint(HERE / "fixtures" / "journal.jsonl")

    def test_committed_tasks_are_left_out(self):
        self.assertNotIn("T001", self.cp["tasks"])

    def test_approved_but_uncommitted_keeps_verdict(self):
        # The commit agent for T002 was started but never returned.
        t = self.cp["tasks"]["T002"]
        self.assertEqual(t["verdict"]["verdict"], "approve")
        self.assertEqual(t["impl"]["files_changed"], ["b.py"])
        self.assertIn("commit:T002", self.cp["interrupted_agents"])

    def test_implemented_not_verified(self):
        self.assertEqual(self.cp["tasks"]["T003"], {"attempt": 0, "impl": {"status": "done", "summary": "three", "files_changed": ["c.py"]}})

    def test_rejection_is_kept_for_the_next_attempt(self):
        self.assertEqual(self.cp["tasks"]["T004"]["verdict"]["problems"], ["missing test"])

    def test_cut_off_attempt(self):
        self.assertEqual(self.cp["tasks"]["T005"], {"attempt": 1, "interrupted": "implement"})

    def test_earlier_attempts_files_are_kept(self):
        d = Path(tempfile.mkdtemp())
        j = d / "journal.jsonl"
        ev = [{"type": "started", "key": "1", "label": "impl:T1"},
              {"type": "result", "key": "1", "result": {"status": "done", "summary": "", "files_changed": ["a.py", "pyproject.toml"]}},
              {"type": "started", "key": "2", "label": "verify:T1"},
              {"type": "result", "key": "2", "result": {"verdict": "reject", "problems": ["x"], "checks_run": []}},
              {"type": "started", "key": "3", "label": "impl:T1#2"},
              {"type": "result", "key": "3", "result": {"status": "done", "summary": "", "files_changed": ["a.py"]}}]
        j.write_text("\n".join(json.dumps(e) for e in ev))
        self.assertEqual(sdd.checkpoint(j)["tasks"]["T1"]["files"], ["a.py", "pyproject.toml"])
        shutil.rmtree(d)

    def test_missing_journal(self):
        self.assertEqual(sdd.checkpoint(Path("/nonexistent/journal.jsonl"))["found"], False)


WF_TEST = (HERE / "fixtures" / "wf_test").resolve()


class RunEventsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def workflow(self, name, lines, transcripts=None):
        wf = self.dir / name
        wf.mkdir()
        (wf / "journal.jsonl").write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
        for agent_id, body in (transcripts or {}).items():
            (wf / f"agent-{agent_id}.jsonl").write_text(body, encoding="utf-8")
        return wf

    def test_retry_sequence(self):
        events = sdd.journal_events([WF_TEST])
        self.assertEqual([e["kind"] for e in events],
                         ["task.started", "task.implemented", "task.verified", "task.started",
                          "task.implemented", "task.verified", "task.approved", "group.finished"])
        self.assertEqual([e.get("attempt") for e in events if e["kind"] in ("task.started", "task.implemented")],
                         [1, 1, 2, 2])
        self.assertEqual(events[2]["verdict"], "reject")
        self.assertEqual(events[2]["problems"], ["missing test"])
        self.assertEqual(events[5]["verdict"], "approve")
        self.assertTrue(all(e["id"] == "T001" for e in events[:7]))
        self.assertEqual(events[7]["ids"], ["T001"])
        self.assertNotIn("id", events[7])
        self.assertEqual(events[7]["commits"], ["abc1234"])
        self.assertIs(events[7]["checks_passed"], True)
        self.assertEqual(events[0]["agent_id"], "a1000000000000001")
        self.assertEqual(events[0]["label"], "impl:T001")
        self.assertTrue(all(e["workflow"] == 0 for e in events))
        # started -> first transcript line, result -> last transcript line
        self.assertEqual(events[0]["ts"], "2026-10-27T13:52:01.000Z")
        self.assertEqual(events[1]["ts"], "2026-10-27T13:55:42.000Z")
        self.assertEqual(events[7]["ts"], "2026-10-27T14:11:30.000Z")
        # agents without a transcript file
        self.assertIsNone(events[2]["ts"])
        self.assertTrue(all("ts" in e for e in events))

    def test_blocked_result(self):
        wf = self.workflow("wf_blocked", [
            {"type": "started", "key": "1", "agentId": "b1", "label": "impl:T002"},
            {"type": "result", "key": "1", "agentId": "b1",
             "result": {"status": "blocked", "reason": "needs a mic", "tag": "manual", "question": "record it"}}])
        events = sdd.journal_events([wf])
        self.assertEqual([e["kind"] for e in events], ["task.started", "task.blocked"])
        self.assertEqual((events[1]["id"], events[1]["reason"], events[1]["tag"]), ("T002", "needs a mic", "manual"))

    def test_failed_line(self):
        wf = self.workflow("wf_failed", [
            {"type": "started", "key": "1", "agentId": "c1", "label": "eval:T003"},
            {"type": "failed", "key": "1", "agentId": "c1", "error": "boom"},
            {"type": "started", "key": "2", "agentId": "c2", "label": "impl:T004"},
            {"type": "failed", "key": "2", "agentId": "c2"}])
        events = sdd.journal_events([wf])
        self.assertEqual([(e["kind"], e["id"]) for e in events],
                         [("task.started", "T003"), ("task.failed", "T003"), ("task.started", "T004"), ("task.failed", "T004")])
        self.assertEqual(events[1]["error"], "boom")
        self.assertEqual(events[3]["error"], "")

    def test_unparseable_and_unknown_lines_are_skipped(self):
        wf = self.dir / "wf_bad"
        wf.mkdir()
        (wf / "journal.jsonl").write_text(
            'not json\n{"type":"started","key":"1","agentId":"d1","label":"other:T1"}\n'
            '{"type":"started","key":"2","agentId":"d2","label":"impl:T005"}\n{"type":"res\n', encoding="utf-8")
        self.assertEqual([e["kind"] for e in sdd.journal_events([wf])], ["task.started"])

    def test_repair_and_blocked_recorded(self):
        wf = self.workflow("wf_repair", [
            {"type": "started", "key": "1", "agentId": "e1", "label": "repair:T1,T2"},
            {"type": "started", "key": "2", "agentId": "e2", "label": "commit:T1,T2"},
            {"type": "result", "key": "2", "agentId": "e2",
             "result": {"checks_ran": True, "checks_passed": False, "failing_output": "x", "commits": [],
                        "blocked_recorded": ["T1", "T2"]}}])
        events = sdd.journal_events([wf])
        self.assertEqual([e["kind"] for e in events], ["group.repair", "group.finished", "task.blocked", "task.blocked"])
        self.assertEqual(events[0]["ids"], ["T1", "T2"])
        self.assertEqual([e["id"] for e in events[2:]], ["T1", "T2"])

    def test_last_line_reads_only_the_tail(self):
        big = json.dumps({"timestamp": "first", "pad": "x" * 100_000}) + "\n" + json.dumps({"timestamp": "last"}) + "\n"
        wf = self.workflow("wf_big", [
            {"type": "started", "key": "1", "agentId": "f1", "label": "impl:T6"},
            {"type": "result", "key": "1", "agentId": "f1", "result": {"status": "done"}}], {"f1": big})
        self.assertEqual([e["ts"] for e in sdd.journal_events([wf])], ["first", "last"])

    def test_valid_workflow_dir(self):
        self.assertTrue(sdd.valid_workflow_dir(WF_TEST))
        self.assertFalse(sdd.valid_workflow_dir(Path("tests/fixtures/wf_test")))  # relative
        (self.dir / "wf_empty").mkdir()
        self.assertFalse(sdd.valid_workflow_dir(self.dir / "wf_empty"))  # no journal.jsonl
        other = self.dir / "notwf"
        other.mkdir()
        (other / "journal.jsonl").write_text("", encoding="utf-8")
        self.assertFalse(sdd.valid_workflow_dir(other))
        self.assertFalse(sdd.valid_workflow_dir(self.dir / "wf_absent"))

    def write_listing(self, run_id, dirs):
        runs = self.dir / "project" / ".sdd" / "runs" / run_id
        runs.mkdir(parents=True)
        (runs / "workflows.json").write_text(json.dumps({"workflow_dirs": [str(d) for d in dirs]}), encoding="utf-8")
        return self.dir / "project"

    def test_run_events_missing_dirs(self):
        other = self.dir / "notwf"
        other.mkdir()
        (other / "journal.jsonl").write_text("", encoding="utf-8")
        (self.dir / "wf_nojournal").mkdir()
        dirs = [other, self.dir / "wf_nojournal", WF_TEST]
        project = self.write_listing("20261027-1400", dirs)
        result = sdd.run_events(project, "20261027-1400")
        self.assertEqual(result["run_id"], "20261027-1400")
        self.assertEqual(result["workflow_dirs"], [str(d) for d in dirs])
        self.assertEqual(result["missing"], [str(other), str(self.dir / "wf_nojournal")])
        self.assertEqual(len(result["events"]), 8)
        self.assertTrue(all(e["workflow"] == 2 for e in result["events"]))

    def test_run_events_without_listing(self):
        (self.dir / "project").mkdir()
        self.assertEqual(sdd.run_events(self.dir / "project", "20261027-1400"),
                         {"run_id": "20261027-1400", "workflow_dirs": [], "missing": [], "events": []})

    def test_cli(self):
        project = self.write_listing("20261027-1401", [WF_TEST])
        out = subprocess.run([sys.executable, str(SCRIPT), "events", str(project), "20261027-1401"],
                             capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout), sdd.run_events(project, "20261027-1401"))


if __name__ == "__main__":
    unittest.main()
