"""Validated evidence contracts and deterministic matching (spec §21).

No model calls or file writes. Call validate_profile with its configuration
directory and validate_job_requirements with the current JD at input boundaries.
"""
import hashlib
import json
import math
import re
from pathlib import Path, PureWindowsPath

from chunker import normalize

STATUSES = {"confirmed", "pending", "disputed"}


def _fail(message):
    raise ValueError(message)


def _object(value, label):
    if not isinstance(value, dict):
        _fail(f"{label}: expected object")
    return value


def _list(value, label, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        _fail(f"{label}: expected {'nonempty ' if nonempty else ''}array")
    return value


def _text(value, label, nonempty=True):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        _fail(f"{label}: expected {'nonempty ' if nonempty else ''}string")
    return value


def _integer(value, label, low, high=None):
    if type(value) is not int or value < low or (high is not None and value > high):
        _fail(f"{label}: invalid integer")
    return value


def _schema(value, label):
    _object(value, label)
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        _fail(f"{label}: schema_version must be 1")


def _unique(value, seen, label):
    _text(value, label)
    if value in seen:
        _fail(f"{label}: duplicate id {value}")
    seen.add(value)


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail(f"{label}: expected lowercase SHA256")


def _status(value, label):
    if not isinstance(value, str) or value not in STATUSES:
        _fail(f"{label}: invalid status")


def _newline(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strict_json(path):
    """Read JSON rejecting nonfinite numbers and duplicate object keys."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_pairs_hook=pairs,
                      parse_constant=lambda value: _fail(f"invalid JSON number: {value}"))


def canonical_hash(value):
    """Hash JSON content, independent of dictionary insertion order."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def jd_hash(text):
    _text(text, "jd", nonempty=False)
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def validate_catalog(catalog):
    _schema(catalog, "catalog")
    _text(catalog.get("version"), "catalog.version")
    module_ids, criterion_ids = set(), set()
    for module in _list(catalog.get("modules"), "catalog.modules", True):
        _object(module, "module")
        _unique(module.get("id"), module_ids, "module.id")
        _text(module.get("name"), "module.name")
        weight = module.get("weight")
        if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
            _fail("module.weight: expected positive finite number")
        for criterion in _list(module.get("criteria"), "module.criteria", True):
            _object(criterion, "criterion")
            _unique(criterion.get("id"), criterion_ids, "criterion.id")
            _text(criterion.get("name"), "criterion.name")
            anchors = _list(criterion.get("anchors"), "criterion.anchors")
            if len(anchors) != 5:
                _fail("criterion.anchors: expected exactly five levels")
            for anchor in anchors:
                _text(anchor, "criterion.anchor")
    return catalog


def _criterion_map(catalog):
    return {criterion["id"]: module["id"] for module in catalog["modules"]
            for criterion in module["criteria"]}


def validate_profile(profile, catalog, base_dir, check_sources=True):
    validate_catalog(catalog)
    _schema(profile, "profile")
    candidate = _object(profile.get("candidate"), "profile.candidate")
    for key, value in candidate.items():
        _text(key, "candidate field")
        _text(value, f"candidate.{key}", nonempty=False)
    criteria = _criterion_map(catalog)
    sources, source_texts = {}, {}
    source_ids = set()
    for source in _list(profile.get("sources"), "profile.sources"):
        _object(source, "source")
        _unique(source.get("id"), source_ids, "source.id")
        filename = _text(source.get("path"), "source.path")
        if Path(filename).is_absolute() or PureWindowsPath(filename).drive or PureWindowsPath(filename).root:
            _fail("source.path: must be relative to config directory")
        _sha(source.get("sha256"), "source.sha256")
        sources[source["id"]] = source
        if check_sources:
            try:
                content = (Path(base_dir) / filename).read_bytes()
                decoded = content.decode("utf-8-sig")
            except (OSError, UnicodeError) as exc:
                _fail(f"source {source['id']}: cannot read UTF-8 source: {exc}")
            if hashlib.sha256(content).hexdigest() != source["sha256"]:
                _fail(f"source {source['id']}: SHA256 changed; reassessment required")
            source_texts[source["id"]] = _newline(decoded).splitlines()
    experience_ids, fact_ids, facts = set(), set(), {}
    for experience in _list(profile.get("experiences"), "profile.experiences"):
        _object(experience, "experience")
        _unique(experience.get("id"), experience_ids, "experience.id")
        for field in ("title", "organization", "period", "role"):
            _text(experience.get(field), f"experience.{field}", nonempty=(field == "title"))
        for fact in _list(experience.get("facts"), "experience.facts"):
            _object(fact, "fact")
            _unique(fact.get("id"), fact_ids, "fact.id")
            _text(fact.get("text"), "fact.text")
            if fact.get("kind", "evidence") not in ("evidence", "boundary"):
                _fail("fact.kind: expected evidence/boundary")
            _status(fact.get("status"), "fact.status")
            refs = _list(fact.get("sources"), "fact.sources", fact["status"] == "confirmed")
            for ref in refs:
                _object(ref, "fact source")
                source_id = _text(ref.get("source_id"), "fact.source_id")
                if source_id not in sources:
                    _fail(f"fact.source_id: unknown source {source_id}")
                start = _integer(ref.get("start_line"), "start_line", 1)
                end = _integer(ref.get("end_line"), "end_line", start)
                quote = _text(ref.get("quote"), "quote")
                if check_sources:
                    lines = source_texts[source_id]
                    if end > len(lines) or _newline(quote) != "\n".join(lines[start - 1:end]):
                        _fail(f"fact {fact['id']}: source lines/quote do not match")
            facts[fact["id"]] = fact
    for assessment in _list(profile.get("assessments"), "profile.assessments"):
        _object(assessment, "assessment")
        criterion_id = _text(assessment.get("criterion_id"), "assessment.criterion_id")
        if criterion_id not in criteria:
            _fail(f"assessment: unknown criterion {criterion_id}")
        _text(assessment.get("context"), "assessment.context", False)
        _status(assessment.get("status"), "assessment.status")
        level = assessment.get("level")
        if "level" not in assessment:
            _fail("assessment.level: missing")
        if level is not None:
            _integer(level, "assessment.level", 0, 4)
        if assessment["status"] == "confirmed" and level is None:
            _fail("confirmed assessment requires a level")
        _text(assessment.get("reason"), "assessment.reason")
        refs = _list(assessment.get("fact_ids"), "assessment.fact_ids", assessment["status"] == "confirmed")
        seen = set()
        for fact_id in refs:
            _unique(fact_id, seen, "assessment.fact_id")
            if fact_id not in facts:
                _fail(f"assessment: unknown fact {fact_id}")
    return profile


def _validate_job_structure(job_record, catalog):
    _object(job_record, "job")
    _text(job_record.get("job_id"), "job.job_id")
    _sha(job_record.get("jd_hash"), "job.jd_hash")
    module_ids = {module["id"] for module in catalog["modules"]}
    criteria = _criterion_map(catalog)
    requirement_ids = set()
    unmapped = _list(job_record.get("unmapped_requirements", []), "job.unmapped_requirements")
    unmapped_ids = set()
    for item in unmapped:
        _object(item, "unmapped requirement")
        _unique(item.get("id"), unmapped_ids, "unmapped requirement.id")
        _text(item.get("text"), "unmapped requirement.text")
        _text(item.get("source_quote"), "unmapped requirement.source_quote")
        if item.get("importance") not in ("required", "preferred"):
            _fail("unmapped requirement.importance: expected required/preferred")
    for requirement in _list(job_record.get("requirements"), "job.requirements", not unmapped):
        _object(requirement, "requirement")
        _unique(requirement.get("id"), requirement_ids, "requirement.id")
        module_id = _text(requirement.get("module_id"), "requirement.module_id")
        if module_id not in module_ids:
            _fail(f"requirement: unknown module {module_id}")
        _text(requirement.get("text"), "requirement.text")
        _text(requirement.get("source_quote"), "requirement.source_quote")
        if requirement.get("importance") not in ("required", "preferred"):
            _fail("requirement.importance: expected required/preferred")
        if type(requirement.get("critical")) is not bool:
            _fail("requirement.critical: expected boolean")
        if requirement.get("mode") not in ("all", "any"):
            _fail("requirement.mode: expected all/any")
        alternatives = set()
        for criterion in _list(requirement.get("criteria"), "requirement.criteria", True):
            _object(criterion, "required criterion")
            criterion_id = _text(criterion.get("criterion_id"), "required criterion.id")
            actual_module = criteria.get(criterion_id)
            if actual_module is None or (requirement["mode"] == "all" and actual_module != module_id):
                _fail(f"requirement: criterion {criterion_id} must belong to module {module_id}")
            alternatives.add(actual_module)
            _integer(criterion.get("required_level"), "required_level", 1, 4)
            _text(criterion.get("context"), "required context", False)
        if requirement["mode"] == "any":
            owner = next(m["id"] for m in catalog["modules"] if m["id"] in alternatives)
            if module_id != owner:
                _fail("any requirement.module_id must be the catalog-first alternative module")
    condition_ids = set()
    for condition in _list(job_record.get("conditions", []), "job.conditions"):
        _object(condition, "condition")
        _unique(condition.get("id"), condition_ids, "condition.id")
        for field in ("type", "text", "source_quote"):
            _text(condition.get(field), f"condition.{field}")
        if condition.get("importance") not in ("required", "preferred"):
            _fail("condition.importance: expected required/preferred")
        if type(condition.get("critical")) is not bool:
            _fail("condition.critical: expected boolean")


def validate_job_requirements(job_record, jd, catalog):
    validate_catalog(catalog)
    _text(jd, "jd")
    _validate_job_structure(job_record, catalog)
    if job_record["jd_hash"] != jd_hash(jd):
        _fail(f"job {job_record['job_id']}: JD hash changed")
    normalized = normalize(jd)
    for requirement in job_record["requirements"]:
        if normalize(requirement["source_quote"]) not in normalized:
            _fail(f"requirement {requirement['id']}: source_quote absent from JD")
    for condition in job_record.get("conditions", []):
        if normalize(condition["source_quote"]) not in normalized:
            _fail(f"condition {condition['id']}: source_quote absent from JD")
    for item in job_record.get("unmapped_requirements", []):
        if normalize(item["source_quote"]) not in normalized:
            _fail(f"unmapped requirement {item['id']}: source_quote absent from JD")
    return job_record


def extraction_cache_key(jd, catalog, rules_version):
    validate_catalog(catalog)
    _text(rules_version, "rules_version")
    return canonical_hash({"jd_hash": jd_hash(jd), "catalog": catalog,
                           "rules_version": rules_version})


def scoring_cache_key(job_record, profile, catalog, rules_version="1"):
    _text(rules_version, "rules_version")
    return canonical_hash({"job": job_record, "profile": profile, "catalog": catalog,
                           "rules_version": rules_version})


def _deduplicate(requirements):
    result = {}
    for requirement in requirements:
        criteria = tuple(sorted({(c["criterion_id"], c["required_level"], c["context"])
                                 for c in requirement["criteria"]}))
        # Repeating a weaker ALL threshold must not raise a score. A stronger
        # preferred threshold remains distinct from the required baseline.
        signature = (tuple((c, x) for c, _, x in criteria), requirement["importance"]) if requirement["mode"] == "all" else criteria
        key = (requirement["module_id"], requirement["mode"], signature)
        if key not in result:
            result[key] = {**requirement, "criteria": [dict(criterion_id=c, required_level=l, context=x)
                                                       for c, l, x in criteria],
                           "ids": [], "source_quotes": []}
        merged = result[key]
        if requirement["mode"] == "all":
            levels = {(c["criterion_id"], c["context"]): c["required_level"] for c in merged["criteria"]}
            for c, level, context in criteria:
                levels[c, context] = max(levels[c, context], level)
            merged["criteria"] = [dict(criterion_id=c, required_level=l, context=x)
                                  for (c, x), l in sorted(levels.items())]
        merged["ids"].append(requirement["id"])
        if requirement["source_quote"] not in merged["source_quotes"]:
            merged["source_quotes"].append(requirement["source_quote"])
        if requirement["importance"] == "required":
            merged["importance"] = "required"
        merged["critical"] = merged["critical"] or requirement["critical"]
    output = list(result.values())
    dropped = set()
    for i, preferred in enumerate(output):
        if preferred["mode"] != "all" or preferred["importance"] != "preferred":
            continue
        p = {(c["criterion_id"], c["context"]): c["required_level"] for c in preferred["criteria"]}
        for required in output:
            if required["module_id"] != preferred["module_id"] or required["mode"] != "all" or required["importance"] != "required":
                continue
            r = {(c["criterion_id"], c["context"]): c["required_level"] for c in required["criteria"]}
            if p.keys() == r.keys() and all(p[k] <= r[k] for k in p):
                required["ids"].extend(preferred["ids"])
                required["source_quotes"] = list(dict.fromkeys(required["source_quotes"] + preferred["source_quotes"]))
                required["critical"] = required["critical"] or preferred["critical"]
                dropped.add(i)
                break
    return [r for i, r in enumerate(output) if i not in dropped]


def score_job(job_record, profile, catalog):
    """Score validated records; current-source/JD validation belongs to caller.

    All public scores, bounds, coverage and module caps use percentage points.
    Per-criterion and per-requirement satisfaction use [0, 1].
    """
    validate_profile(profile, catalog, None, check_sources=False)
    _validate_job_structure(job_record, catalog)
    facts = {fact["id"]: fact for event in profile["experiences"] for fact in event["facts"]}
    fact_events = {fact["id"]: event["title"] for event in profile["experiences"] for fact in event["facts"]}
    criterion_names = {c["id"]: c["name"] for m in catalog["modules"] for c in m["criteria"]}
    levels, evidence, reasons = {}, {}, {}
    disputed = {(a["criterion_id"], a["context"]) for a in profile["assessments"]
                if a["status"] == "disputed" or any(facts[f]["status"] == "disputed" for f in a["fact_ids"])}
    for assessment in profile["assessments"]:
        if assessment["status"] != "confirmed" or any(facts[f]["status"] != "confirmed" for f in assessment["fact_ids"]):
            continue
        key = (assessment["criterion_id"], assessment["context"])
        if key in disputed:
            continue
        if key not in levels or assessment["level"] > levels[key]:
            levels[key] = assessment["level"]
            evidence[key] = set(assessment["fact_ids"])
            reasons[key] = {assessment["reason"]}
        elif assessment["level"] == levels[key]:
            evidence[key].update(assessment["fact_ids"])
            reasons[key].add(assessment["reason"])
    evaluated, unknowns, critical_gaps = [], [], []
    for requirement in _deduplicate(job_record["requirements"]):
        components = []
        for criterion in requirement["criteria"]:
            key = (criterion["criterion_id"], criterion["context"])
            level = levels.get(key)
            lower = 0.0 if level is None else min(level / criterion["required_level"], 1.0)
            upper = 1.0 if level is None else lower
            component = {**criterion, "level": level, "lower": lower, "upper": upper,
                         "name": criterion_names[criterion["criterion_id"]],
                         "fact_ids": sorted(evidence.get(key, set())),
                         "reason": "\n".join(sorted(reasons.get(key, set()))),
                         "evidence": [{"fact_id": fid, "experience_title": fact_events[fid],
                                       "text": facts[fid]["text"]}
                                      for fid in sorted(evidence.get(key, set()))]}
            components.append(component)
            if level is None:
                unknowns.append({"requirement_ids": requirement["ids"], **criterion})
        aggregate = min if requirement["mode"] == "all" else max
        lower = aggregate(c["lower"] for c in components)
        upper = aggregate(c["upper"] for c in components)
        evaluated.append({**requirement, "criteria": components, "lower": lower, "upper": upper,
                          "known": lower == upper,
                          "satisfaction": lower if lower == upper else None,
                          "weight": 1.0 if requirement["importance"] == "required" else 0.5})
        if requirement["critical"] and lower < 1:
            critical_gaps.append({"requirement_ids": requirement["ids"], "text": requirement["text"],
                                  "status": "gap" if upper < 1 else "unknown",
                                  "lower": lower, "upper": upper})
    involved = {requirement["module_id"] for requirement in evaluated}
    budgets = [m for m in catalog["modules"] if m["id"] in involved]
    # Scale before summing to avoid overflow from otherwise finite large weights.
    max_budget = max((m["weight"] for m in budgets), default=1)
    total_budget = sum(m["weight"] / max_budget for m in budgets)
    modules = []
    for module in budgets:
        reqs = [r for r in evaluated if r["module_id"] == module["id"]]
        weight = sum(r["weight"] for r in reqs)
        cap = round(100 * (module["weight"] / max_budget) / total_budget, 10)
        lower = cap * sum(r["weight"] * r["lower"] for r in reqs) / weight
        upper = cap * sum(r["weight"] * r["upper"] for r in reqs) / weight
        determined = sum(r["weight"] for r in reqs if r["lower"] == r["upper"]) / weight
        modules.append({"id": module["id"], "name": module["name"], "cap": cap, "max": cap,
                        "lower": lower, "upper": upper,
                        "score": lower if lower == upper else None,
                        "earned": lower if lower == upper else None,
                        "coverage": 100 * determined})
    lower = min(100.0, sum(m["lower"] for m in modules))
    upper = min(100.0, sum(m["upper"] for m in modules))
    coverage = min(100.0, sum(m["cap"] * m["coverage"] / 100 for m in modules))
    unmapped = [dict(item) for item in job_record.get("unmapped_requirements", [])]
    return {"engine": "evidence", "job_id": job_record["job_id"],
            "score": lower if lower == upper and not unmapped else None,
            "lower": lower if not unmapped else None, "upper": upper if not unmapped else None,
            "coverage": coverage if not unmapped else None,
            "requirements_complete": not unmapped, "unmapped_requirements": unmapped,
            "modules": modules, "requirements": evaluated,
            "conditions": [dict(condition) for condition in job_record.get("conditions", [])],
            "critical_gaps": critical_gaps, "unknowns": unknowns,
            "cache_key": scoring_cache_key(job_record, profile, catalog)}
