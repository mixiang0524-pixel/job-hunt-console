"""Isolated synthetic tests; never reads the candidate's real materials."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import resume_export as resume
from evidence_core import jd_hash, extraction_cache_key


def fixture(base):
    """All text and identities are explicitly fictional layout/test content."""
    base = Path(base)
    (base / "evidence").mkdir(parents=True, exist_ok=True)
    (base / "out").mkdir(exist_ok=True)
    source = "虚构排版样例：在练习项目中整理访谈记录，标注问题并形成需求清单；没有外部上线或业务效果验证。\n"
    (base / "source.txt").write_bytes(source.encode("utf-8"))
    catalog = {"schema_version": 1, "version": "fixture-1", "modules": [{
        "id": "m", "name": "需求工作", "weight": 100, "criteria": [
            {"id": cid, "name": title, "anchors": ["无法完成", "理解", "参与", "独立", "稳定"]}
            for cid, title in (("c", "需求整理"), ("unrelated", "无关能力"))]}]}
    fact = {"id": "f1", "text": source.strip(), "status": "confirmed", "sources": [{
        "source_id": "s1", "start_line": 1, "end_line": 1, "quote": source.strip()}]}
    profile = {"schema_version": 1, "candidate": {"name": "虚构候选人 · 排版验证", "email": "sample@example.invalid", "location": "样例城市", "private_note": "不进入任务"},
        "sources": [{"id": "s1", "path": "source.txt", "sha256": hashlib.sha256(source.encode()).hexdigest()}],
        "experiences": [{"id": "e1", "title": "虚构练习项目", "organization": "样例组织", "period": "样例时间", "role": "练习参与者", "facts": [fact, {**copy.deepcopy(fact), "id": "f2", "text": "无关经历"}]}],
        "assessments": [{"criterion_id": "c", "context": "", "level": 2, "status": "confirmed", "fact_ids": ["f1"], "reason": "仅练习，无外部成果"},
                        {"criterion_id": "unrelated", "context": "", "level": 1, "status": "confirmed", "fact_ids": ["f2"], "reason": "无关事实"}]}
    job = {"job_id": "fictional-job", "title": "需求分析实习岗位（虚构）", "company": "虚构公司", "jd": "能够整理访谈记录与需求清单。"}
    req = {"schema_version": 1, "jobs": [{"job_id": job["job_id"], "jd_hash": jd_hash(job["jd"]), "requirements": [{
        "id": "r1", "module_id": "m", "text": "整理访谈", "source_quote": "整理访谈记录", "importance": "required", "critical": False,
        "mode": "all", "criteria": [{"criterion_id": "c", "required_level": 2, "context": ""}]}]}]}
    req["cache"] = {job["job_id"]: extraction_cache_key(job["jd"], catalog, resume.EXTRACTION_RULES)}
    for path, content in (("evidence/capabilities.json", catalog), ("evidence/profile.json", profile),
                          ("evidence/requirements.json", req), ("out/rows.json", [job])):
        (base / path).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = {"_base_dir": str(base), "judge": {"model": "fixture-model"}, "output_dir": "./out", "evidence": {}}
    (base / "config.json").write_text(json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg, job["job_id"]


def result_for(task):
    return {"schema_version": 1, "job_id": task["job_id"], "profile_hash": task["profile_hash"], "jd_hash": task["jd_hash"], "task_hash": task["task_hash"],
            "headline": task["job"]["title"], "sections": [{"title": "项目练习", "items": [{"text": task["facts"][0]["text"], "fact_ids": ["f1"]}]}]}


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg, self.job = fixture(self.temp.name)
        self.task = resume.build_task(self.cfg, self.job)

    def change_json(self, relative, fn):
        path = Path(self.temp.name) / relative
        obj = json.loads(path.read_text(encoding="utf-8"))
        fn(obj)
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def setup_export(self):
        resume.write_task(self.cfg, self.job)
        directory = resume.job_directory(self.cfg, self.job)
        (directory / "result.json").write_text(json.dumps(result_for(self.task), ensure_ascii=False), encoding="utf-8")
        return directory

    def test_only_relevant_confirmed_facts_and_contact(self):
        self.assertEqual(self.task["allowed_fact_ids"], ["f1"])
        self.assertEqual([f["id"] for f in self.task["facts"]], ["f1"])
        self.assertNotIn("private_note", self.task["candidate"])
        self.assertNotIn("experiences", self.task)

    def test_related_confirmed_boundaries_are_not_lost_by_capability_filter(self):
        def add(p):
            boundary = {**copy.deepcopy(p["experiences"][0]["facts"][0]),
                        "id": "scope", "kind": "boundary", "text": "仅练习，无外部验证"}
            p["experiences"][0]["facts"].append(boundary)
            p["experiences"].append({**copy.deepcopy(p["experiences"][0]), "id": "other-event",
                "facts": [{**copy.deepcopy(boundary), "id": "unrelated-boundary"}]})
        self.change_json("evidence/profile.json", add)
        task = resume.build_task(self.cfg, self.job)
        self.assertEqual(task["allowed_fact_ids"], ["f1", "scope"])
        self.assertNotIn("unrelated-boundary", task["allowed_fact_ids"])

    def test_unconfirmed_boundary_not_sent_to_resume(self):
        def add(p):
            p["experiences"][0]["facts"].append({**copy.deepcopy(p["experiences"][0]["facts"][0]),
                "id": "scope", "kind": "boundary", "status": "pending"})
        self.change_json("evidence/profile.json", add)
        self.assertEqual(resume.build_task(self.cfg, self.job)["allowed_fact_ids"], ["f1"])

    def test_unconfigured_model(self):
        self.cfg["judge"] = {}
        with self.assertRaises(ValueError):
            resume.build_task(self.cfg, self.job)

    def test_wrong_context_not_retrieved(self):
        self.change_json("evidence/profile.json", lambda p: p["assessments"][0].update(context="different"))
        with self.assertRaises(ValueError):
            resume.build_task(self.cfg, self.job)

    def test_disputed_fact_not_retrieved(self):
        self.change_json("evidence/profile.json", lambda p: p["experiences"][0]["facts"][0].update(status="disputed"))
        with self.assertRaises(ValueError):
            resume.build_task(self.cfg, self.job)

    def test_disputed_assessment_blocks_old_confirmed(self):
        self.change_json("evidence/profile.json", lambda p: p["assessments"].append(
            {**p["assessments"][0], "status": "disputed", "reason": "新发现的归属争议"}))
        with self.assertRaisesRegex(ValueError, "暂无相关"):
            resume.build_task(self.cfg, self.job)

    def test_stale_requirements_cannot_create_new_resume_task(self):
        self.change_json("evidence/capabilities.json", lambda c: c.update(version="changed"))
        with self.assertRaisesRegex(ValueError, "变化"):
            resume.build_task(self.cfg, self.job)

    def test_bad_or_empty_refs_rejected(self):
        for ids in ([], ["f2"], ["missing"], [12], "f1"):
            with self.subTest(ids=ids):
                result = result_for(self.task)
                result["sections"][0]["items"][0]["fact_ids"] = ids
                with self.assertRaises(ValueError):
                    resume.validate_result(result, self.task)

    def test_wrong_hash_rejected(self):
        for key in ("profile_hash", "jd_hash", "job_id", "task_hash"):
            result = result_for(self.task)
            result[key] = "stale"
            with self.assertRaises(ValueError):
                resume.validate_result(result, self.task)

    def test_empty_result_rejected(self):
        for key, value in (("sections", []), ("headline", ""), ("schema_version", 2)):
            result = result_for(self.task)
            result[key] = value
            with self.assertRaises(ValueError):
                resume.validate_result(result, self.task)

    def test_changed_source_rejected(self):
        self.setup_export()
        (Path(self.temp.name) / "source.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            resume.export_resume(self.cfg, self.job)

    def test_changed_catalog_rejects_old_task(self):
        self.setup_export()
        self.change_json("evidence/capabilities.json", lambda c: c.update(version="changed"))
        with self.assertRaisesRegex(ValueError, "变化"):
            resume.export_resume(self.cfg, self.job)

    def test_modified_task_allowlist_rejected(self):
        directory = self.setup_export()
        task = json.loads((directory / "task.json").read_text(encoding="utf-8"))
        task["allowed_fact_ids"].append("f2")
        (directory / "task.json").write_text(json.dumps(task), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "变化"):
            resume.export_resume(self.cfg, self.job)

    def test_exports_without_internal_ids_and_keeps_inputs(self):
        directory = self.setup_export()
        before = {str(p): p.read_bytes() for p in Path(self.temp.name).rglob("*.json")}
        with patch.object(resume, "render_pdf", side_effect=lambda p, *args: Path(p).write_bytes(b"%PDF-mock")):
            outputs = resume.export_resume(self.cfg, self.job)
        self.assertEqual(len(outputs), 4)
        self.assertIn("resume-editor.html", outputs)
        self.assertNotIn("f1", (directory / "resume-draft.md").read_text(encoding="utf-8"))
        review = json.loads((directory / "resume-review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["items"][0]["facts"][0]["id"], "f1")
        self.assertEqual(review["sources"][0]["path"], "source.txt")
        for p, content in before.items():
            self.assertEqual(Path(p).read_bytes(), content)

    def test_pdf_failure_does_not_publish_partial_outputs(self):
        directory = self.setup_export()
        with patch.object(resume, "render_pdf", side_effect=ValueError("no font")):
            with self.assertRaises(ValueError):
                resume.export_resume(self.cfg, self.job)
        self.assertFalse((directory / "resume-draft.md").exists())

    def test_hidden_intent_does_not_become_missing_name(self):
        result = result_for(self.task)
        text = resume.render_markdown({}, result)
        self.assertNotIn(result["headline"], text)
        self.assertNotIn("求职意向", text)
        self.assertIn("求职意向", resume.render_markdown({}, result, True))


if __name__ == "__main__":
    unittest.main()
