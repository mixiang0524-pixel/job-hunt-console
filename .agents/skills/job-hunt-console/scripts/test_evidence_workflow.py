"""隔离文件任务协议、确认边界及缓存回归，不访问真实资料。"""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from evidence_core import canonical_hash, jd_hash, strict_json
from evidence_workflow import Workflow, init_workspace, score_current


def catalog():
    return {"schema_version": 1, "version": "test-1", "modules": [
        {"id": "m", "name": "样例模块", "weight": 1, "criteria": [
            {"id": "c", "name": "样例能力", "anchors": ["不能", "理解", "参与", "独立", "稳定"]}]}]}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "访谈.md"
        self.source.write_text("我独立完成了样例任务。\n这只是隔离测试。\n第三行补充。\n", encoding="utf-8")
        self.cfg = {"_base_dir": str(self.root), "output_dir": "out",
                    "judge": {"model": "configured-test-model"},
                    "evidence": {"source_files": ["访谈.md"]}}
        self.w = Workflow(self.cfg)
        init_workspace(self.cfg)
        self.w.write(self.w.catalog_path, catalog())
        self.w.write(self.root / "out" / "rows.json", [
            {"job_id": "j1", "title": "样例岗位", "jd": "独立完成样例任务。"},
            {"job_id": "j2", "title": "另一岗位", "jd": "参与样例任务。"}])
        self.w.sources()

    def manifest(self, kind):
        return strict_json(self.w.task_dir(kind) / "manifest.json")

    def profile_result(self, confirmed=False):
        manifest = self.manifest("interview")
        profile = strict_json(self.w.profile_path)
        profile["sources"] = manifest["sources"]
        source_id = profile["sources"][0]["id"]
        profile["experiences"] = [{"id": "e", "title": "测试项目", "organization": "", "period": "", "role": "",
                                   "facts": [{"id": "f", "text": "独立完成样例任务", "status": "confirmed" if confirmed else "pending",
                                              "sources": [{"source_id": source_id, "start_line": 1, "end_line": 1,
                                                           "quote": "我独立完成了样例任务。"}]}]}]
        profile["assessments"] = [{"criterion_id": "c", "context": "", "level": 3, "status": "confirmed" if confirmed else "pending",
                                    "fact_ids": ["f"], "reason": "只用于测试"}]
        return {"task_id": manifest["task_id"], "profile_hash": manifest["profile_hash"],
                "catalog_hash": manifest["catalog_hash"], "profile": profile}

    def requirements_result(self):
        manifest = self.manifest("requirements")
        rows = self.w.rows()
        return {"task_id": manifest["task_id"], "catalog_hash": manifest["catalog_hash"], "jobs": [
            {"job_id": job["job_id"], "jd_hash": job["jd_hash"], "requirements": [
                {"id": "r", "module_id": "m", "text": "样例要求", "source_quote": rows[job["job_id"]]["jd"],
                 "importance": "required", "critical": True, "mode": "all", "criteria": [
                     {"criterion_id": "c", "required_level": 3, "context": ""}]}]}
            for job in manifest["jobs"]]}

    def confirmed_profile(self):
        self.w.interview_task()
        self.w.apply_profile(self.profile_result(True), ["f"])

    def extracted(self):
        self.w.job_task("requirements")
        self.w.apply_requirements(self.requirements_result())

    def test_init_never_overwrites(self):
        before = self.w.catalog_path.read_bytes()
        self.assertEqual(init_workspace(self.cfg)["created"], [])
        self.assertEqual(self.w.catalog_path.read_bytes(), before)

    def test_sources_keeps_old_hash_and_profile_untouched(self):
        before = self.w.profile_path.read_bytes()
        old = strict_json(self.w.index_path)
        self.source.write_text("改变后的材料", encoding="utf-8")
        output = self.w.sources()
        self.assertEqual(output["changed_requires_review"], [old["sources"][0]["id"]])
        self.assertEqual(strict_json(self.w.index_path), old)
        self.assertEqual(self.w.profile_path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.w.interview_task()

    def test_sources_reject_binary_and_invalid_utf8(self):
        self.w.opts["source_files"] = ["private.jpg"]
        with self.assertRaises(ValueError):
            self.w.sources()
        self.source.write_bytes(b"\xff")
        self.w.opts["source_files"] = ["访谈.md"]
        with self.assertRaises(UnicodeError):
            self.w.sources()

    def test_model_required_without_default(self):
        self.cfg["judge"] = {}
        with self.assertRaisesRegex(ValueError, "judge.model"):
            self.w.interview_task()
        with self.assertRaises(ValueError):
            self.w.job_task("requirements")

    def test_interview_budget_preserves_whole_lines_and_continuation(self):
        self.w.opts["interview_char_budget"] = len("我独立完成了样例任务。")
        self.w.interview_task()
        snippets = self.manifest("interview")["snippets"]
        self.assertEqual(snippets[0]["end_line"], 1)
        self.assertEqual(snippets[0]["remaining_from_line"], 2)
        with self.assertRaises(ValueError):
            self.w.interview_task()

    def test_explicit_source_ranges(self):
        ident = strict_json(self.w.index_path)["sources"][0]["id"]
        self.w.interview_task([ident + ":2:3"])
        self.assertEqual(self.manifest("interview")["snippets"][0]["quote"], "这只是隔离测试。\n第三行补充。")

    def test_all_source_packets_preserve_entire_selected_range(self):
        self.w.opts["interview_char_budget"] = len("我独立完成了样例任务。")
        ident = strict_json(self.w.index_path)["sources"][0]["id"]
        self.w.interview_task([ident + ":1:3"], all_sources=True)
        manifest = self.manifest("interview")
        lines = []
        for entry in manifest["packets"]:
            packet = strict_json(self.w.task_dir("interview") / entry["file"])
            self.assertEqual(len(packet["snippets"]), 1)
            snippet = packet["snippets"][0]
            self.assertLessEqual(len(snippet["quote"]), self.w.opts["interview_char_budget"])
            lines.extend(range(snippet["start_line"], snippet["end_line"] + 1))
        self.assertEqual(lines, [1, 2, 3])
        self.assertEqual(len(manifest["snippets"]), len(manifest["packets"]))

    def test_all_sources_overlong_line_fails_before_publishing(self):
        self.w.opts["interview_char_budget"] = 1
        with self.assertRaisesRegex(ValueError, "整行"):
            self.w.interview_task(all_sources=True)
        self.assertFalse((self.w.task_dir("interview") / "manifest.json").exists())

    def test_model_cannot_confirm_new_facts(self):
        self.w.interview_task()
        before = self.w.profile_path.read_bytes()
        result = self.profile_result(True)
        with self.assertRaisesRegex(ValueError, "用户确认"):
            self.w.apply_profile(result)
        self.assertTrue((self.w.task_dir("interview") / "manifest.json").exists())
        self.assertEqual(self.w.profile_path.read_bytes(), before)
        self.w.apply_profile(result, ["f"])
        self.assertEqual(strict_json(self.w.profile_path)["experiences"][0]["facts"][0]["status"], "confirmed")

    def test_pending_import_stays_pending(self):
        self.w.interview_task()
        self.w.apply_profile(self.profile_result())
        self.assertEqual(strict_json(self.w.profile_path)["experiences"][0]["facts"][0]["status"], "pending")

    def test_forged_quote_rejected(self):
        self.w.interview_task()
        result = self.profile_result()
        result["profile"]["experiences"][0]["facts"][0]["sources"][0]["quote"] = "伪造"
        with self.assertRaises(ValueError):
            self.w.apply_profile(result)

    def test_input_change_rejects_result(self):
        self.w.interview_task()
        result = self.profile_result()
        self.source.write_text("改变", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.w.apply_profile(result)

    def test_confirmed_assessment_cannot_use_pending_fact(self):
        self.w.interview_task()
        result = self.profile_result()
        result["profile"]["assessments"][0]["status"] = "confirmed"
        with self.assertRaisesRegex(ValueError, "未确认事实"):
            self.w.apply_profile(result)

    def test_requirements_input_full_jd_even_over_budget(self):
        self.w.opts["jd_batch_char_budget"] = 2
        self.w.job_task("requirements")
        manifest = self.manifest("requirements")
        self.assertEqual(len(manifest["packets"]), 2)
        packet = strict_json(self.w.task_dir("requirements") / "input-001.json")
        self.assertEqual(packet["jobs"][0]["jd"], self.w.rows()["j1"]["jd"])

    def test_missing_extra_duplicate_jobs_rejected_atomically(self):
        self.w.job_task("requirements")
        result = self.requirements_result()
        before = self.w.requirements_path.read_bytes()
        for jobs in (result["jobs"][:1], result["jobs"] + result["jobs"][:1]):
            bad = {**result, "jobs": jobs}
            with self.assertRaises(ValueError):
                self.w.apply_requirements(bad)
            self.assertEqual(self.w.requirements_path.read_bytes(), before)
        self.w.apply_requirements(result)

    def test_stale_jd_and_catalog_rejected(self):
        self.w.job_task("requirements")
        result = self.requirements_result()
        rows = list(self.w.rows().values())
        rows[0]["jd"] += "新增要求"
        self.w.write(self.root / "out" / "rows.json", rows)
        with self.assertRaises(ValueError):
            self.w.apply_requirements(result)
        updated = catalog()
        updated["modules"][0]["weight"] = 2
        self.w.write(self.w.catalog_path, updated)
        with self.assertRaises(ValueError):
            self.w.apply_requirements(result)

    def test_cache_reuses_jd_after_profile_update(self):
        self.extracted()
        self.confirmed_profile()
        output = self.w.job_task("requirements")
        self.assertEqual(output, {"cache_hits": 2, "pending": 0})

    def test_score_full_chain_does_not_touch_legacy(self):
        self.confirmed_profile()
        self.extracted()
        legacy = self.root / "out" / "matched.json"
        legacy.write_text("old data", encoding="utf-8")
        results = score_current(self.cfg)
        self.assertEqual(results["j1"]["score"], 100)
        self.assertEqual(legacy.read_text(encoding="utf-8"), "old data")
        self.source.write_text("changed", encoding="utf-8")
        before = (self.root / "out" / "evidence-matched.json").read_bytes()
        with self.assertRaises(ValueError):
            score_current(self.cfg)
        self.assertEqual((self.root / "out" / "evidence-matched.json").read_bytes(), before)

    def test_score_unknown_and_absent_are_not_zero(self):
        self.w.job_task("requirements", ["j1"])
        self.w.apply_requirements(self.requirements_result())
        result = score_current(self.cfg)
        self.assertIsNone(result["j1"]["score"])
        self.assertNotIn("j2", result)

    def test_market_bootstrap_empty_catalog(self):
        self.w.write(self.w.catalog_path, {"schema_version": 1, "version": "draft-1", "modules": []})
        with self.assertRaises(ValueError):
            self.w.job_task("requirements")
        self.w.job_task("market", limit=1)
        manifest = self.manifest("market")
        self.w.apply_catalog({"task_id": manifest["task_id"], "catalog_hash": manifest["catalog_hash"],
                              "job_hashes": {j["job_id"]: j["jd_hash"] for j in manifest["jobs"]},
                              "catalog": catalog()})
        self.assertEqual(self.w.catalog(), catalog())


if __name__ == "__main__":
    unittest.main()
