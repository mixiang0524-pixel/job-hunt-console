"""打分：匹配分 + 公司质量分 + 混合分。

匹配分只回答『岗位要求和我的经历对不对得上』。
公司质量分只回答『这家公司值不值得去』。
两个分数互不污染，混合只在展示层可选。

薪资不参与任何计算——它是展示列和手动排序项。

scoring.match_engine=evidence 时按 spec §21 从已确认经历与岗位要求确定性重算。
未迁移的配置继续读取 judge_apply.py 的块匹配结果；两种口径不混用。

## 门槛扣分全部删除（§11.1）

v1 有六项：硕士 / 限定专业 / 985211 / 届别 / 经验深度 / 出勤。全删。

理由不是「做不动」，是**做不准而且收益低**：
「985/211 优先」不等于不要；写「计算机相关专业」的软件产品岗非科班照样能投；
真正卡死的只有算法、医学这类，而这类岗位本来就不多。
点进去一眼就能自己判，标个「不感兴趣」即可——**扣分做不准，就不如不做**。

保留的是**反门槛**（§11.2）：JD 白纸黑字写「不看专业」这类，判定简单。
但它不进匹配分——匹配分只回答「我和这岗位的要求对不对得上」，
反门槛回答的是「我够不够格」，混在一起分数就不好解释。
改成打 tag + 混合排序时加 `scoring.anti_barrier_bonus` 分（可关）。
"""
import argparse
import re

from taxonomy import ANTI_BARRIERS

from common import (KeywordMatcher, die, info, jd_len, length_band, load_config,
                    load_json, out_dir, save_json, warn, shared_path)

# ------------------------------------------------------------ 公司质量分档位

SCALE_SCORE = [
    (r"10000", 20), (r"1000-9999", 18), (r"500-999", 16),
    (r"100-499", 13), (r"20-99", 5), (r"0-20", 0),
]
SCALE_UNKNOWN = 8

STAGE_SCORE = [
    (r"已上市", 15), (r"不需要融资", 12), (r"[DEF]轮|战略融资", 14),
    (r"C轮", 13), (r"B轮", 12), (r"A轮", 10), (r"天使", 7), (r"未融资", 5),
]
STAGE_UNKNOWN = 7

ACTIVE_SCORE = [
    (r"刚刚|在线", 10), (r"今日|1日", 9), (r"3日", 8), (r"本周|7日", 7),
    (r"本月|30日", 5), (r"半年|3月", 2),
]
ACTIVE_UNKNOWN = 4

TECH_INDUSTRY = r"互联网|计算机|软件|信息技术|人工智能|数据服务|电子商务|游戏|通信"

# ------------------------------------------------------------ 公司质量分

def score_company(job, intel, cfg):
    """公司质量分 0-100。返回 (分数, 明细, 是否检索过)。"""
    detail = {}
    detail["规模"] = _table(job.get("company_scale"), SCALE_SCORE, SCALE_UNKNOWN)
    detail["融资"] = _table(job.get("company_stage"), STAGE_SCORE, STAGE_UNKNOWN)
    detail["HR活跃"] = _table(job.get("boss_active_status"), ACTIVE_SCORE, ACTIVE_UNKNOWN)

    core = cfg.get("keywords", {}).get("core", [])
    ind = job.get("company_industry") or ""
    if core and KeywordMatcher(core).hits(ind):
        detail["行业相关"] = 15
    elif re.search(TECH_INDUSTRY, ind):
        detail["行业相关"] = 11
    else:
        detail["行业相关"] = 7 if ind else 5

    local = sum(detail.values())

    company = job.get("company") or job.get("boss_name") or ""
    rec = (intel or {}).get(company)
    if rec and rec.get("confidence") not in (None, "not_found"):
        s = rec.get("score") or {}
        detail["口碑"] = s.get("reputation", 8)
        detail["业务匹配"] = s.get("business_fit", 8)
        detail["风险"] = s.get("risk", 10)
        return round(local + detail["口碑"] + detail["业务匹配"] + detail["风险"], 1), detail, True

    # 未检索：只有本地 60 分，等比放大到 100 制并标记
    return round(local * 100 / 60, 1), detail, False


def _table(value, table, default):
    v = value or ""
    for pat, sc in table:
        if re.search(pat, v):
            return sc
    return default


# ------------------------------------------------------------ 混合分

def blend(match, company, band, anti_bonus, cfg):
    """混合分。JD 越短，公司质量分的权重越高。

    短 JD 的匹配分被系统性低估——不是岗位差，是**信息少**。
    这时候「这家公司值不值得去」是更可靠的信号。
    """
    b = cfg.get("scoring", {}).get("blend", {})
    w = b.get(band, 0.3)
    base = max(0.0, min(100.0, match)) * (1 - w) + company * w
    return round(min(100.0, base + anti_bonus), 1)


# ------------------------------------------------------------ 主流程

def main():
    ap = argparse.ArgumentParser(description="匹配分 + 公司质量分 + 混合分")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    args = ap.parse_args()
    cfg = load_config(args.config, args.track)
    sc = cfg.get("scoring", {})

    rows = load_json(out_dir(cfg, "rows.json"))
    if not rows:
        die("找不到 rows.json。先跑 pipeline.py --phase apply。")

    engine = sc.get("match_engine", "blocks")
    if engine not in ("blocks", "evidence"):
        die("scoring.match_engine 只能为 blocks 或 evidence")
    if engine == "evidence":
        from evidence_workflow import score_current
        try:
            matched = score_current(cfg)
        except (ValueError, OSError) as exc:
            die(f"经历匹配未能完成：{exc}")
    else:
        matched = load_json(out_dir(cfg, "matched.json"), None)
        if matched is None:
            die("找不到 matched.json。匹配分是块级语义判定，脚本算不了。"
                " 跑 python judge_task.py --pass match 产出任务包，判完跑 judge_apply.py。")

    intel = load_json(shared_path(cfg, "company_intel.json"), {}) or {}
    bonus_on = sc.get("anti_barrier_bonus", 3)
    no_intel = no_match = 0

    for r in rows:
        m = matched.get(r["job_id"])
        c, cdetail, queried = score_company(r, intel, cfg)
        band = length_band(r, cfg)
        tags = r.get("tags") or []
        # 反门槛只加在**混合分**上，不进匹配分。匹配分回答「对不对得上」，
        # 反门槛回答「够不够格」，混在一起分数就没法解释了。
        anti = bonus_on if any(t in ANTI_BARRIERS for t in tags) else 0

        # 不让上一次生成残留的字段跨计分引擎冒充本次结论。
        r.pop("evidence_match", None)
        r["match_engine"] = engine
        if m and engine == "evidence":
            r["evidence_match"] = m
            r["match_score"] = m["score"]
            r["match_doubt"] = bool(m["unknowns"])
            r["match_doubt_why"] = "部分要求仍需补充经历" if m["unknowns"] else ""
            r["match_hits"] = r["match_misses"] = []
            r["match_n"] = len(m["requirements"])
            if m["score"] is None:
                no_match += 1
        elif m:
            r["match_score"] = m["score"]
            r["match_doubt"] = m["doubt"]
            r["match_doubt_why"] = m.get("doubt_why", "")
            r["match_hits"] = [b for b in m["blocks"] if b["hit"] != "没有"]
            r["match_misses"] = [b for b in m["blocks"] if b["hit"] == "没有"]
            r["match_n"] = len(m["blocks"])
        else:
            # 没匹配结论的不给 0 分——0 分意味着「判过了，不匹配」，
            # 这是「还没判」。混在一起会让它们在排序里假装成最差的一批。
            r["match_score"] = None
            r["match_doubt"] = True
            r["match_doubt_why"] = "还没跑匹配判定"
            r["match_hits"] = r["match_misses"] = []
            r["match_n"] = 0
            no_match += 1

        r["match_max"] = 100
        r["anti_barrier_bonus"] = anti
        r["company_score"] = c
        r["company_detail"] = cdetail
        r["company_queried"] = queried
        r["jd_len"] = jd_len(r)
        r["length_band"] = band
        r["blend_score"] = (blend(r["match_score"], c, band, anti, cfg)
                            if r["match_score"] is not None else None)
        if not queried:
            no_intel += 1

    # 排序：没判过的沉到最后（不是当成 0 分，是当成「未知」）；
    # 判定强度参与同分段内排序，拿不准的往后但不丢掉。
    rank = {"保留": 0, "保留·职责偏离": 1, "需整篇判定": 2}
    rows.sort(key=lambda r: (r["match_score"] is None,
                             -(r["match_score"] or 0),
                             rank.get(r.get("family_verdict"), 0),
                             -r["company_score"]))
    save_json(out_dir(cfg, "scored.json"), rows)

    scored = [r for r in rows if r["match_score"] is not None]
    info(f"打分完成: {len(rows)} 行，其中 {len(scored)} 行有匹配分")
    if scored:
        ss = sorted((r["match_score"] for r in scored), reverse=True)
        nd = sum(1 for r in scored if r["match_doubt"])
        info(f"  匹配分 最高 {ss[0]} / 中位 {ss[len(ss)//2]} / 最低 {ss[-1]}"
             f"，存疑 {nd} 行（只标虚线，不改分数）")
        nb = sum(1 for r in scored if r["anti_barrier_bonus"])
        info(f"  反门槛加分 {nb} 行（+{bonus_on}，只加在混合分上，不进匹配分）")
    if no_match:
        next_step = ("用 evidence_workflow.py 补充岗位要求与经历档案。" if engine == "evidence"
                     else "跑 judge_task.py --pass match 补上。")
        warn(f"{no_match} 行还没有完整匹配分，已排到最后（不是 0 分，是未知）。" + next_step)
    info(f"未检索公司情报: {no_intel} 行（公司质量分按本地 60 分等比放大，已标记）")
    short = sum(1 for r in rows if r["length_band"] == "short")
    info(f"JD 短于 {sc.get('jd_short_threshold', 300)} 字: {short} 行"
         f" —— 这批信息少，混合分里公司质量分权重更高；"
         f"跑 enrich_company.py 补情报能进一步改善")
    info(f"→ {out_dir(cfg, 'scored.json')}")


if __name__ == "__main__":
    main()
