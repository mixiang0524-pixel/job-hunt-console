"""Isolated fixtures for evidence score semantics; no personal data or network."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from evidence_core import (canonical_hash, extraction_cache_key, jd_hash, score_job,
                           strict_json, validate_catalog, validate_job_requirements,
                           validate_profile)


def criterion(key):
    return {"id": key, "name": key, "anchors": [f"anchor {i}" for i in range(5)]}


def requirement(key="r1", criteria=None, module="m1", mode="all", critical=False):
    return {"id": key, "module_id": module, "text": "requirement", "source_quote": "要求",
            "importance": "required", "critical": critical, "mode": mode,
            "criteria": criteria or [{"criterion_id": "c1", "required_level": 4, "context": ""}]}


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.raw = "第一行\r\n完成甲事项\r\n完成乙事项\r\n".encode("utf-8")
        (self.base / "source.txt").write_bytes(self.raw)
        self.catalog = {"schema_version": 1, "version": "1", "modules": [
            {"id": "m1", "name": "module 1", "weight": 3, "criteria": [criterion("c1"), criterion("c2")]},
            {"id": "m2", "name": "module 2", "weight": 1, "criteria": [criterion("c3")]}]}
        self.profile = {"schema_version": 1, "candidate": {"name": "样例", "other": "任意字段"},
                        "sources": [{"id": "s1", "path": "source.txt", "sha256": hashlib.sha256(self.raw).hexdigest()}],
                        "experiences": [{"id": "e1", "title": "样例经历", "organization": "", "period": "", "role": "",
                                         "facts": [self.fact("f1", 2, "完成甲事项"), self.fact("f2", 3, "完成乙事项")]}],
                        "assessments": [self.assessment("c1", 3, "f1")]}
        self.jd = "要求：事项甲、事项乙"
        self.job = {"job_id": "job", "jd_hash": jd_hash(self.jd), "requirements": [requirement()]}

    @staticmethod
    def fact(key, line, quote):
        return {"id": key, "text": quote, "status": "confirmed", "sources": [
            {"source_id": "s1", "start_line": line, "end_line": line, "quote": quote}]}

    @staticmethod
    def assessment(key, level, fact, context="", status="confirmed"):
        return {"criterion_id": key, "context": context, "level": level,
                "status": status, "fact_ids": [fact], "reason": "fixture evidence"}

    def score(self):
        return score_job(self.job, self.profile, self.catalog)

    def test_valid_contract_and_normalization(self):
        self.assertIs(validate_catalog(self.catalog), self.catalog)
        self.assertIs(validate_profile(self.profile, self.catalog, self.base), self.profile)
        self.assertIs(validate_job_requirements(self.job, self.jd, self.catalog), self.job)
        self.assertEqual(jd_hash("⼯作"), jd_hash("工作"))
        self.assertEqual(self.score()["score"], 75)

    def test_non_scoring_conditions_preserved_without_claiming_match(self):
        before = self.score()
        self.job["conditions"] = [{"id": "eligibility", "type": "education",
            "text": "学历条件待本人核对", "source_quote": "要求", "importance": "required", "critical": True}]
        validate_job_requirements(self.job, self.jd, self.catalog)
        after = self.score()
        self.assertEqual(after["conditions"], self.job["conditions"])
        self.assertEqual(after["score"], before["score"])
        self.assertEqual(after["coverage"], before["coverage"])
        self.assertNotIn("status", after["conditions"][0])

    def test_unmapped_ability_withholds_total_even_when_mapped_part_is_known(self):
        self.job["unmapped_requirements"] = [{"id": "gap", "text": "目录缺少维度",
            "source_quote": "事项乙", "importance": "required"}]
        validate_job_requirements(self.job, self.jd, self.catalog)
        result = self.score()
        self.assertFalse(result["requirements_complete"])
        self.assertIsNone(result["score"])
        self.assertIsNone(result["lower"])
        self.assertIsNone(result["upper"])
        self.assertIsNone(result["coverage"])
        self.assertEqual(result["modules"][0]["score"], 75)
        self.assertEqual(result["unmapped_requirements"], self.job["unmapped_requirements"])
        self.job["unmapped_requirements"][0]["source_quote"] = "原文不存在"
        with self.assertRaises(ValueError):
            validate_job_requirements(self.job, self.jd, self.catalog)

    def test_all_unmapped_requirements_remain_unknown_not_zero(self):
        self.job["requirements"] = []
        self.job["unmapped_requirements"] = [{"id": "gap", "text": "目录缺少维度",
            "source_quote": "事项乙", "importance": "preferred"}]
        validate_job_requirements(self.job, self.jd, self.catalog)
        result = self.score()
        self.assertIsNone(result["score"])
        self.assertEqual(result["modules"], [])

    def test_repeated_weaker_threshold_does_not_raise_score(self):
        before = self.score()
        weak = copy.deepcopy(self.job["requirements"][0])
        weak["id"] = "weaker-repetition"
        weak["criteria"][0]["required_level"] = 1
        self.job["requirements"].append(weak)
        after = self.score()
        self.assertEqual(after["score"], before["score"])
        self.assertEqual(len(after["requirements"]), 1)
        self.assertEqual(after["requirements"][0]["ids"], ["r1", "weaker-repetition"])

    def test_weaker_preferred_duplicate_not_an_extra_budget(self):
        weak = copy.deepcopy(self.job["requirements"][0])
        weak.update(id="preferred-repetition", importance="preferred")
        weak["criteria"][0]["required_level"] = 1
        self.job["requirements"].append(weak)
        self.assertEqual(self.score()["score"], 75)
        self.assertEqual(len(self.score()["requirements"]), 1)

    def test_stricter_preferred_level_does_not_upgrade_required_baseline(self):
        self.job["requirements"][0]["criteria"][0]["required_level"] = 2
        preferred = copy.deepcopy(self.job["requirements"][0])
        preferred.update(id="advanced", importance="preferred")
        preferred["criteria"][0]["required_level"] = 4
        self.job["requirements"].append(preferred)
        requirements = self.score()["requirements"]
        self.assertEqual(len(requirements), 2)
        self.assertEqual(next(r for r in requirements if r["importance"] == "required")["lower"], 1)
        self.assertEqual(next(r for r in requirements if r["importance"] == "preferred")["lower"], .75)

    def test_non_scoring_condition_invalid_reference_rejected(self):
        self.job["conditions"] = [{"id": "eligibility", "type": "education",
            "text": "条件", "source_quote": "不在原文中", "importance": "required", "critical": True}]
        with self.assertRaisesRegex(ValueError, "condition eligibility: source_quote absent"):
            validate_job_requirements(self.job, self.jd, self.catalog)

    def test_non_scoring_condition_invalid_contract_rejected(self):
        condition = {"id": "eligibility", "type": "education", "text": "条件",
                     "source_quote": "要求", "importance": "required", "critical": True}
        for field, value in [("type", ""), ("importance", "must"), ("critical", 1)]:
            with self.subTest(field=field):
                self.job["conditions"] = [{**condition, field: value}]
                with self.assertRaises(ValueError):
                    validate_job_requirements(self.job, self.jd, self.catalog)
        self.job["conditions"] = [condition, condition.copy()]
        with self.assertRaisesRegex(ValueError, "duplicate id"):
            validate_job_requirements(self.job, self.jd, self.catalog)

    def test_duplicate_requirements_and_evidence_cannot_inflate(self):
        second = requirement("r2", [{"criterion_id": "c2", "required_level": 4, "context": ""}])
        self.job["requirements"].append(second)
        self.profile["assessments"].append(self.assessment("c2", 0, "f2"))
        before = self.score()
        for index in range(10):
            self.job["requirements"].append(requirement(f"dup{index}"))
            self.profile["assessments"].append(copy.deepcopy(self.profile["assessments"][0]))
        after = self.score()
        self.assertEqual(before["score"], after["score"])
        self.assertEqual(len(after["requirements"]), 2)

    def test_dedup_criteria_order_importance_and_critical(self):
        c2 = {"criterion_id": "c2", "required_level": 4, "context": ""}
        self.job["requirements"][0]["criteria"].append(c2)
        self.job["requirements"][0]["importance"] = "preferred"
        duplicate = copy.deepcopy(self.job["requirements"][0])
        duplicate.update(id="r2", importance="required", critical=True)
        duplicate["criteria"].reverse()
        self.job["requirements"].append(duplicate)
        result = self.score()
        self.assertEqual(len(result["requirements"]), 1)
        self.assertEqual(result["requirements"][0]["weight"], 1)
        self.assertTrue(result["critical_gaps"])

    def test_complementary_evidence_increases_coverage(self):
        self.job["requirements"].append(requirement("r2", [{"criterion_id": "c2", "required_level": 4, "context": ""}]))
        before = self.score()
        self.assertIsNone(before["score"])
        self.assertEqual(before["coverage"], 50)
        self.profile["assessments"].append(self.assessment("c2", 4, "f2"))
        after = self.score()
        self.assertEqual(after["score"], 87.5)
        self.assertEqual(after["coverage"], 100)

    def test_all_and_any(self):
        self.job["requirements"][0]["criteria"].append({"criterion_id": "c2", "required_level": 4, "context": ""})
        self.profile["assessments"].append(self.assessment("c2", 1, "f2"))
        self.assertEqual(self.score()["score"], 25)
        self.job["requirements"][0]["mode"] = "any"
        self.assertEqual(self.score()["score"], 75)

    def test_cross_module_any_uses_best_branch_once(self):
        self.job["requirements"][0].update(mode="any", criteria=[
            {"criterion_id": "c3", "required_level": 2, "context": ""},
            {"criterion_id": "c1", "required_level": 4, "context": ""}])
        self.profile["assessments"].append(self.assessment("c3", 2, "f2"))
        result = self.score()
        self.assertEqual(result["score"], 100)
        self.assertEqual(len(result["modules"]), 1)
        self.assertEqual(result["modules"][0]["cap"], 100)
        duplicate = copy.deepcopy(self.job["requirements"][0])
        duplicate["id"] = "reordered"
        duplicate["criteria"].reverse()
        self.job["requirements"].append(duplicate)
        self.assertEqual(len(self.score()["requirements"]), 1)

    def test_cross_module_any_owner_is_stable_and_all_cannot_mix_modules(self):
        req = self.job["requirements"][0]
        req.update(mode="any", module_id="m2", criteria=[
            {"criterion_id": "c3", "required_level": 2, "context": ""},
            {"criterion_id": "c1", "required_level": 4, "context": ""}])
        with self.assertRaisesRegex(ValueError, "catalog-first"):
            self.score()
        req.update(mode="all", module_id="m1")
        with self.assertRaisesRegex(ValueError, "must belong"):
            self.score()

    def test_any_full_match_resolves_unknown_alternative(self):
        self.profile["assessments"][0]["level"] = 4
        self.job["requirements"][0]["mode"] = "any"
        self.job["requirements"][0]["criteria"].append({"criterion_id": "c2", "required_level": 4, "context": ""})
        result = self.score()
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["coverage"], 100)
        self.assertEqual(len(result["unknowns"]), 1)

    def test_all_zero_resolves_unknown_but_retains_gap(self):
        self.profile["assessments"][0]["level"] = 0
        self.job["requirements"][0]["critical"] = True
        self.job["requirements"][0]["criteria"].append({"criterion_id": "c2", "required_level": 4, "context": ""})
        result = self.score()
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["critical_gaps"][0]["status"], "gap")

    def test_module_budget_and_critical_gap_preserved(self):
        self.job["requirements"].append(requirement("r2", [{"criterion_id": "c3", "required_level": 2, "context": ""}], "m2", critical=True))
        self.profile["assessments"][0]["level"] = 4
        self.profile["assessments"].append(self.assessment("c3", 0, "f2"))
        result = self.score()
        self.assertEqual(result["score"], 75)
        self.assertEqual([m["cap"] for m in result["modules"]], [75, 25])
        self.assertEqual(result["critical_gaps"][0]["status"], "gap")

    def test_unknown_is_not_zero_and_context_is_exact(self):
        self.job["requirements"][0]["criteria"][0]["context"] = "special context"
        result = self.score()
        self.assertIsNone(result["score"])
        self.assertEqual((result["lower"], result["upper"], result["coverage"]), (0, 100, 0))

    def test_pending_disputed_and_pending_fact_are_unknown(self):
        for status in ("pending", "disputed"):
            self.profile["assessments"][0]["status"] = status
            self.assertIsNone(self.score()["score"])
        self.profile["assessments"][0]["status"] = "confirmed"
        self.profile["experiences"][0]["facts"][0]["status"] = "pending"
        self.assertIsNone(self.score()["score"])

    def test_dispute_blocks_old_confirmed_assessment(self):
        self.profile["assessments"].append(self.assessment("c1", None, "f2", status="disputed"))
        self.assertIsNone(self.score()["score"])

    def test_highest_confirmed_level_not_sum(self):
        self.profile["assessments"].append(self.assessment("c1", 2, "f2"))
        self.assertEqual(self.score()["score"], 75)

    def test_sources_mutated_and_wrong_quote_rejected(self):
        self.profile["experiences"][0]["facts"][0]["sources"][0]["quote"] = "another quote"
        with self.assertRaisesRegex(ValueError, "quote"):
            validate_profile(self.profile, self.catalog, self.base)
        self.profile["experiences"][0]["facts"][0]["sources"][0]["quote"] = "完成甲事项"
        (self.base / "source.txt").write_bytes(self.raw + b"modified")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            validate_profile(self.profile, self.catalog, self.base)

    def test_multiline_quote_and_line_bounds(self):
        ref = self.profile["experiences"][0]["facts"][0]["sources"][0]
        ref.update(start_line=2, end_line=3, quote="完成甲事项\r\n完成乙事项")
        validate_profile(self.profile, self.catalog, self.base)
        ref["end_line"] = 999
        with self.assertRaises(ValueError):
            validate_profile(self.profile, self.catalog, self.base)

    def test_jd_change_and_unlocatable_quote_rejected(self):
        with self.assertRaises(ValueError):
            validate_job_requirements(self.job, self.jd + "changed", self.catalog)
        self.job["requirements"][0]["source_quote"] = "not present"
        with self.assertRaises(ValueError):
            validate_job_requirements(self.job, self.jd, self.catalog)

    def test_cache_depends_on_jd_catalog_rules_not_profile(self):
        before = extraction_cache_key(self.jd, self.catalog, "1")
        old_score_key = self.score()["cache_key"]
        self.profile["candidate"]["name"] = "changed"
        self.assertEqual(before, extraction_cache_key(self.jd, self.catalog, "1"))
        self.assertNotEqual(old_score_key, self.score()["cache_key"])
        self.assertNotEqual(before, extraction_cache_key(self.jd + "new", self.catalog, "1"))
        self.assertNotEqual(before, extraction_cache_key(self.jd, self.catalog, "2"))
        self.catalog["modules"][0]["criteria"][0]["anchors"][0] = "new anchor"
        self.assertNotEqual(before, extraction_cache_key(self.jd, self.catalog, "1"))

    def test_invalid_catalog_types_numbers_and_duplicate_ids(self):
        for weight in (True, float("nan"), float("inf"), 0, -1, "3"):
            damaged = copy.deepcopy(self.catalog)
            damaged["modules"][0]["weight"] = weight
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                validate_catalog(damaged)
        self.catalog["modules"][1]["criteria"][0]["id"] = "c1"
        with self.assertRaises(ValueError):
            validate_catalog(self.catalog)

    def test_invalid_level_reference_and_zero_without_fact(self):
        assessment = self.profile["assessments"][0]
        for level in (True, -1, 5, 1.5, "3", None):
            assessment["level"] = level
            with self.subTest(level=level), self.assertRaises(ValueError):
                validate_profile(self.profile, self.catalog, self.base)
        assessment.update(level=0, fact_ids=[])
        with self.assertRaises(ValueError):
            validate_profile(self.profile, self.catalog, self.base)
        assessment["fact_ids"] = ["missing"]
        with self.assertRaises(ValueError):
            validate_profile(self.profile, self.catalog, self.base)

    def test_invalid_requirement_criteria_rejected(self):
        self.job["requirements"][0]["criteria"][0]["criterion_id"] = "c3"
        with self.assertRaises(ValueError):
            self.score()
        self.job["requirements"] = []
        with self.assertRaises(ValueError):
            self.score()

    def test_candidate_arbitrary_string_fields_and_html_preserved(self):
        self.profile["candidate"]["custom"] = "<script>display must escape me</script>"
        validate_profile(self.profile, self.catalog, self.base)
        self.profile["candidate"]["custom"] = 4
        with self.assertRaises(ValueError):
            validate_profile(self.profile, self.catalog, self.base)

    def test_strict_json_and_canonical_hash(self):
        path = self.base / "bad.json"
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                strict_json(path)
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))
        with self.assertRaises(ValueError):
            canonical_hash({"a": float("nan")})


if __name__ == "__main__":
    unittest.main()
