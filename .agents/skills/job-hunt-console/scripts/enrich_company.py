"""外部公司情报补全。

脚本本身不联网——检索是 AI/人的活。它负责两件事：
    --emit-task   算出哪些公司需要查、去重、跳过已缓存的，产出待查清单
    --merge       把查回来的结果并进 company_intel.json

同一家公司只查一次。查不到也要写进缓存（confidence: not_found），
否则每轮都会重查同一家查不到的公司。
"""
import argparse

from common import (info, jd_len, load_config, load_json, now, out_dir,
                    save_json, today, warn, shared_path)


def needs_research(job, cfg):
    """触发条件。返回触发原因，不触发返回 None。"""
    sc = cfg.get("scoring", {})
    short = sc.get("jd_short_threshold", 300)
    n = jd_len(job)

    if n < short:
        return f"JD 仅 {n} 字，短于 {short} 字阈值"
    scale = (job.get("company_scale") or "").strip()
    if not scale or "未知" in scale:
        return "公司规模字段缺失"
    jd = job.get("jd") or ""
    if jd.rstrip().endswith(("...", "…", "查看更多", "展开")):
        return "JD 疑似被截断"
    return None


def emit_task(cfg):
    rows = load_json(out_dir(cfg, "rows.json"))
    if not rows:
        warn("找不到 rows.json，先跑 pipeline.py --phase finalize。")
        return
    intel = load_json(shared_path(cfg, "company_intel.json"), {}) or {}

    wanted = {}
    skipped_cached = 0
    for r in rows:
        reason = needs_research(r, cfg)
        if not reason:
            continue
        name = r.get("company") or r.get("boss_name") or ""
        if not name:
            continue
        if name in intel:
            skipped_cached += 1
            continue
        e = wanted.setdefault(name, {
            "company": name,
            "company_link": r.get("company_link"),
            "reasons": set(),
            "self_reported_scale": r.get("company_scale"),
            "self_reported_stage": r.get("company_stage"),
            "industry": r.get("company_industry"),
            "sample_titles": [],
        })
        e["reasons"].add(reason)
        if len(e["sample_titles"]) < 3:
            e["sample_titles"].append(r.get("title"))

    items = [{**v, "reasons": sorted(v["reasons"])} for v in wanted.values()]
    task = {
        "generated_at": now(),
        "instruction": (
            "逐家检索并填写 result。要查的：\n"
            "  headcount   实际人数（与自报规模互相印证，冲突时以检索为准并在 note 说明）\n"
            "  funding     融资情况与最近融资时间\n"
            "  business    在营业务，是否贴合目标岗位方向与候选人履历\n"
            "  reputation  网络评价/口碑\n"
            "  negatives   欠薪、裁员、劳动纠纷、经营异常、被执行\n"
            "  score       {reputation:0-15, business_fit:0-15, risk:0-10}\n"
            "  confidence  high | low | not_found\n"
            "查不到就写 confidence=not_found 并如实说明，不要用推测填充。"
        ),
        "count": len(items),
        "items": items,
    }
    path = out_dir(cfg, "company_task.json")
    save_json(path, task)
    info(f"需要检索 {len(items)} 家公司（另有 {skipped_cached} 家命中缓存，跳过）")
    info(f"→ 待查清单: {path}")
    info(f"→ 查完写成 company_result.json，然后: python enrich_company.py --merge")


def merge(cfg):
    result = load_json(out_dir(cfg, "company_result.json"))
    if not result:
        warn("找不到 company_result.json。")
        return
    intel = load_json(shared_path(cfg, "company_intel.json"), {}) or {}

    records = result.get("items", result) if isinstance(result, dict) else result
    added = 0
    for rec in records:
        name = rec.get("company")
        if not name:
            continue
        rec.setdefault("queried_at", today())
        rec.setdefault("confidence", "low")
        rec.setdefault("score", {"reputation": 8, "business_fit": 8, "risk": 10})
        intel[name] = {k: v for k, v in rec.items() if k != "company"}
        added += 1

    save_json(shared_path(cfg, "company_intel.json"), intel)
    nf = sum(1 for v in intel.values() if v.get("confidence") == "not_found")
    info(f"并入 {added} 家，缓存共 {len(intel)} 家（其中查不到的 {nf} 家已记入，不会重查）")
    info("→ 重新跑 score.py 让公司质量分用上这批情报")


def main():
    ap = argparse.ArgumentParser(description="公司情报补全（脚本不联网，只管清单与缓存）")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--emit-task", action="store_true")
    g.add_argument("--merge", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config, args.track)
    emit_task(cfg) if args.emit_task else merge(cfg)


if __name__ == "__main__":
    main()
