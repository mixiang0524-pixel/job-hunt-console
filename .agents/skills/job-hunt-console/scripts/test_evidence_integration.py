"""Exercise the real CLI boundary with isolated, fictional inputs."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from evidence_core import extraction_cache_key, jd_hash
from evidence_workflow import RULES_VERSION, Workflow
from test_resume_export import fixture

SCRIPTS = Path(__file__).resolve().parent


def integrated_fixture(base):
    cfg, _ = fixture(base)
    cfg.update(scoring={"match_engine": "evidence"}, keywords={"core": []},
               target_role="样例方向", cities=["样例城市"])
    cfg["_config_path"] = str(Path(base) / "config.json")
    save = lambda name, value: (Path(base) / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    save("config.json", {k: v for k, v in cfg.items() if not k.startswith("_")})
    catalog = json.loads((Path(base) / "evidence/capabilities.json").read_text(encoding="utf-8"))
    doc = json.loads((Path(base) / "evidence/requirements.json").read_text(encoding="utf-8"))
    req = doc["jobs"][0]
    rows, jobs = [], []
    for ident, criterion, level, context in [("known", "c", 2, ""), ("gap", "unrelated", 4, ""), ("unknown", "c", 2, "新场景")]:
        row = {"job_id": ident, "title": "虚构验证岗位 " + ident, "company": "样例公司", "city": "样例城市",
               "jd": "能够整理访谈记录与需求清单。场景标识：" + ident, "job_link": "https://example.invalid/job/" + ident,
               "salary": "仅排版验证", "first_seen": "2026-10-03"}
        item = copy.deepcopy(req)
        item["job_id"] = ident
        item["jd_hash"] = jd_hash(row["jd"])
        item["requirements"][0].update(critical=True)
        item["requirements"][0]["criteria"][0].update(criterion_id=criterion, required_level=level, context=context)
        rows.append(row)
        jobs.append(item)
    save("out/rows.json", rows)
    save("evidence/requirements.json", {"schema_version": 1, "jobs": jobs,
         "cache": {r["job_id"]: extraction_cache_key(r["jd"], catalog, RULES_VERSION) for r in rows}})
    return cfg


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cfg = integrated_fixture(self.base)

    def run_cli(self, script, *args):
        return subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / script),
                               "--config", self.cfg["_config_path"], *args], capture_output=True,
                              text=True, encoding="utf-8")

    def test_scoring_and_console_keep_unknown_separate(self):
        state = self.base / "state.json"
        state.write_text('{"manual":"preserve"}', encoding="utf-8")
        result = self.run_cli("score.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads((self.base / "out/scored.json").read_text(encoding="utf-8"))
        self.assertEqual([r["match_score"] for r in rows], [100, 25, None])
        self.assertIsNone(rows[-1]["blend_score"])
        self.assertTrue(rows[1]["evidence_match"]["critical_gaps"])
        self.assertEqual(self.run_cli("build_console.py").returncode, 0)
        html = (self.base / "out/投递台.html").read_text(encoding="utf-8")
        self.assertIn('"evidence_match"', html)
        self.assertIn("虚构排版样例", html)
        self.assertEqual(state.read_text(encoding="utf-8"), '{"manual":"preserve"}')

    def test_daily_rebuild_cache_hit_completes_without_model(self):
        result = self.run_cli("daily.py", "--rebuild")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.base / "out/投递台.html").exists())
        self.assertFalse((self.base / "evidence/requirements/manifest.json").exists())

    def test_source_change_aborts_before_replacing_scores(self):
        self.assertEqual(self.run_cli("score.py").returncode, 0)
        old = (self.base / "out/scored.json").read_bytes()
        (self.base / "source.txt").write_text("changed", encoding="utf-8")
        self.assertNotEqual(self.run_cli("score.py").returncode, 0)
        self.assertEqual((self.base / "out/scored.json").read_bytes(), old)

    def test_duplicate_jd_new_id_reuses_prior_extraction(self):
        path = self.base / "out/rows.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        rows.append({**rows[0], "job_id": "new-id"})
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        result = Workflow(self.cfg).job_task("requirements", ids=["new-id"])
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["cache_hits"], 1)

    def test_task_archival_keeps_inputs_and_allows_new_task(self):
        flow = Workflow(self.cfg)
        first = flow.job_task("market", limit=1)
        with self.assertRaisesRegex(ValueError, "manifest"):
            flow.job_task("market", limit=1)
        archive = flow.archive_task("market")
        self.assertTrue((Path(archive["archived"]) / "manifest.json").exists())
        second = flow.job_task("market", limit=1)
        self.assertNotEqual(first["task_id"], second["task_id"])

    def test_stage_model_and_effort_are_recorded(self):
        self.cfg["judge"].update(task_models={"market": "task-model"}, reasoning_effort="medium")
        result = Workflow(self.cfg).job_task("market", limit=1)
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["model"], "task-model")
        self.assertEqual(manifest["reasoning_effort"], "medium")


if __name__ == "__main__":
    unittest.main()
