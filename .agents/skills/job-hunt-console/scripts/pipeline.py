"""硬排除 -> 模型逐块判读 -> 结论校验 -> 保守去重。

先运行 --phase filter，再完成 judge_task / judge_apply，最后 --phase apply。
关键词只供明确排除、相关性和展示，不使用命中数量冒充语义判断。"""
import argparse
import csv
import re
from collections import Counter, defaultdict

from common import (KeywordMatcher, city_of, company_key, die, info, jd_len,
                    load_config, load_json, match_text, norm_title, out_dir,
                    save_json, similarity, warn, shared_path)

# 保留但不参与筛选的结论：这两类要人看一眼，不能静默丢掉
KEEP = {"保留", "保留·职责偏离", "需整篇判定"}


# ------------------------------------------------------------------ Stage 0

def stage0(jobs, cfg):
    """硬性排除。只放不会有例外的条件——公司规模不在这里，它是界面开关。"""
    ex = cfg.get("hard_exclude", {})
    kws = [k for k in ex.get("keywords", []) if k]
    companies = set(ex.get("companies", []))
    industries = set(ex.get("industries", []))

    kept, excluded = [], []
    for j in jobs:
        text = f"{j.get('title','')}\n{j.get('jd','')}"
        reason = None
        for k in kws:
            if k in text:
                reason = f"命中排除词「{k}」"
                break
        if not reason and j.get("company") in companies:
            reason = "公司黑名单"
        if not reason and j.get("company_industry") in industries:
            reason = "行业黑名单"

        if reason:
            excluded.append({**j, "_excluded_reason": reason})
        else:
            kept.append(j)
    return kept, excluded


# ------------------------------------------------------------------ 去重

def dedup(rows, cfg):
    """保守自动合并 + 疑似重复标记。"""
    dd = cfg.get("dedup", {})
    auto = dd.get("auto_merge", True)
    thr = dd.get("fuzzy_flag_threshold", 0.8)

    groups = defaultdict(list)
    for r in rows:
        key = (company_key(r), city_of(r), norm_title(r.get("title", "")))
        groups[key].append(r)

    merged = []
    for key, items in groups.items():
        if not auto or len(items) == 1:
            for it in items:
                merged.append(_as_row(it, [it]))
            continue
        # 留判定最强的那条当主帖：保留 > 职责偏离 > 需整篇判定
        items.sort(key=lambda x: (x.get("family_verdict") != "保留",
                                  x.get("family_verdict") != "保留·职责偏离"))
        merged.append(_as_row(items[0], items))

    # 同公司同城内的疑似重复：只标记，不合并
    by_cc = defaultdict(list)
    for r in merged:
        by_cc[(r["company_key"], r["city"])].append(r)
    for _, items in by_cc.items():
        for i in range(len(items)):
            for k in range(i + 1, len(items)):
                a, b = items[i], items[k]
                if similarity(norm_title(a["title"]), norm_title(b["title"])) >= thr:
                    a.setdefault("similar_to", []).append(b["job_id"])
                    b.setdefault("similar_to", []).append(a["job_id"])
    return merged


def _as_row(primary, members):
    hrs = []
    for m in members:
        hrs.append({
            "job_id": m.get("job_id"),
            "boss_name": m.get("boss_name") or m.get("company"),
            "boss_title": m.get("boss_title"),
            "active": m.get("boss_active_status"),
            "link": m.get("job_link"),
            "lid": m.get("lid"),
            "security_id": m.get("security_id"),
            "salary": m.get("salary"),
        })
    salaries = [m.get("salary") for m in members if m.get("salary")]
    return {**primary,
            "company_key": company_key(primary),
            "city": city_of(primary),
            "hr_count": len(members),
            "hr_list": hrs,
            "salary_variants": sorted(set(salaries))}


# ------------------------------------------------------------------ 主流程

def phase_filter(cfg):
    store = load_json(shared_path(cfg, "store.json"), {}) or {}
    jobs = [j for j in store.values() if j.get("job_id")]
    if not jobs:
        die("store.json 是空的。先跑 crawl_once.py。")
    info(f"载入 {len(jobs)} 条")

    kept, excluded = stage0(jobs, cfg)
    save_json(out_dir(cfg, "_excluded.json"), excluded)
    save_json(out_dir(cfg, "stage0_kept.json"), kept)
    info(f"Stage0 硬性排除: 剔除 {len(excluded)}，剩 {len(kept)}"
         f"（已写入 _excluded.json，界面折叠区可见，未删除）")

    info("")
    info(f"→ 下一步: python judge_task.py")


def phase_apply(cfg):
    kept = load_json(out_dir(cfg, "stage0_kept.json"), None)
    if kept is None:
        die("找不到 stage0_kept.json。先跑 python pipeline.py --phase filter。")
    judged = load_json(out_dir(cfg, "judged.json"), None)
    if judged is None:
        die("找不到 judged.json。块级判定是语义判定，脚本不能替。\n"
            "  跑 python judge_task.py 产出任务包，判完再跑 judge_apply.py。")

    gated = {j["job_id"] for j in load_json(out_dir(cfg, "_title_gated.json"), []) or []}

    passed, rejected, unjudged = [], [], []
    for j in kept:
        jid = j.get("job_id")
        if jid in gated:
            continue
        v = judged.get(jid)
        if not v:
            unjudged.append(j)
            continue
        j = {**j,
             "family_verdict": v["verdict"],
             "family_ratio": v["ratio"],
             "family_why": v["why"],
             "families": v.get("families") or {},
             # 二级方向和 tag 是 JD 的固有属性，跟目标岗位无关，
             # 所以它们从 judged.json 直接带过来，换方向也不用重判（§3.2）。
             "tracks": v.get("tracks") or [],
             "tags": v.get("tags") or [],
             "points": sorted({b["pt"] for b in v.get("blocks") or []
                               if b.get("pt")})}
        if v["verdict"] in KEEP:
            passed.append(j)
        else:
            rejected.append({**j, "_reject_reason": f"{v['verdict']}（{v['why']}）"})

    save_json(out_dir(cfg, "_family_rejected.json"), rejected)
    c = Counter(j["family_verdict"] for j in passed)
    info(f"块级判定: 保留 {len(passed)}，判否 {len(rejected)}"
         + (f"，未判 {len(unjudged)}" if unjudged else ""))
    info(f"  其中 保留 {c['保留']} / 职责偏离 {c['保留·职责偏离']} / "
         f"需整篇判定 {c['需整篇判定']}")
    tk = Counter(t for j in passed for t in j["tracks"])
    if tk:
        info("  二级方向: " + "  ".join(f"{k} {v}" for k, v in tk.most_common(10)))
    tg = Counter(t for j in passed for t in j["tags"])
    if tg:
        info("  tag: " + "  ".join(f"{k} {v}" for k, v in tg.most_common(10)))
    if unjudged:
        warn(f"{len(unjudged)} 条既没判也没被闸门挡掉，本轮不进结果。"
             f"多半是判读缺批次——跑 judge_apply.py 看它报的缺批号。")

    # 关键词只做展示：告诉使用者「为什么它匹配上了」，不再当闸门
    kwcfg = cfg.get("keywords", {})
    all_kw = [k for k in (list(kwcfg.get("seed", [])) + list(kwcfg.get("core", []))) if k]
    matcher = KeywordMatcher(all_kw, kwcfg.get("context_guards"))
    for j in passed:
        j["_hits"] = sorted(matcher.hits(match_text(j)))

    rows = dedup(passed, cfg)
    save_json(out_dir(cfg, "rows.json"), rows)
    dupes = sum(r["hr_count"] - 1 for r in rows)
    fuzzy = sum(1 for r in rows if r.get("similar_to"))
    info(f"去重: {len(passed)} 条帖子 -> {len(rows)} 行"
         f"（合并掉 {dupes} 个重复 HR 帖，另标记 {fuzzy} 条疑似重复）")

    _write_freq(cfg, rows, all_kw)
    info("")
    info(f"→ rows.json 已就绪，下一步: python score.py")


def _write_freq(cfg, rows, all_kw):
    """关键词触发频率。现在是**观察指标**，不参与筛选。

    留着是因为它仍然回答一个有用的问题：我配的这些词，哪些根本没在真实
    JD 里出现过？没出现的词说明我对这个岗位的语言习惯理解有偏差。
    """
    seed = set(cfg["keywords"].get("seed", []))
    cnt = Counter()
    for r in rows:
        cnt.update(r.get("_hits", []))
    path = out_dir(cfg, "keyword_freq.csv")
    total = max(len(rows), 1)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["keyword", "is_seed", "hit_rows", "pct_of_rows"])
        for kw in sorted(all_kw, key=lambda k: (-cnt[k], k)):
            w.writerow([kw, "Y" if kw in seed else "N", cnt[kw],
                        f"{cnt[kw] / total:.1%}"])
    dead = [k for k in all_kw if not cnt[k]]
    info(f"关键词触发频率 -> {path}"
         + (f"（{len(dead)} 个词一次都没命中，值得回头看看词配得对不对）" if dead else ""))


def main():
    ap = argparse.ArgumentParser(description="硬排除 -> 块级判定 -> 去重")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    ap.add_argument("--phase", choices=["filter", "apply"], required=True)
    args = ap.parse_args()
    cfg = load_config(args.config, args.track)
    (phase_filter if args.phase == "filter" else phase_apply)(cfg)


if __name__ == "__main__":
    main()
