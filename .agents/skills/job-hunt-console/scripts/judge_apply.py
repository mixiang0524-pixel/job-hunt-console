"""消费判读结果。这是「模型 -> 脚本」那一侧的边界。

    python judge_apply.py --pass annotate    # 类目/要点/主判据/族标签 -> judged.json
    python judge_apply.py --pass match       # 命中判定 -> 匹配分 -> matched.json

**不信任判读方给出的任何岗位级结论，全部重算。**
判读方只做一件事：给每个块打标。
从标签到结论的那套规则是确定性的，必须由代码执行——
让判读方既打标签又套规则，等于把可验证的一步也变成不可验证的。

顺带把「标签不合法」「块数对不上」列出来。这些不影响结论（我们重算/丢弃），
但它是判读质量的信号：形式都错了，内容多半也糙。
"""
import argparse
import glob
import os
from collections import Counter
from datetime import datetime

import taxonomy
from common import (die, info, load_config, load_json, out_dir, resolve,
                    save_json, shared_path, warn)

FAMILIES = {"产品", "开发", "设计", "运营", "数据", "测试", "销售", "职能", "其他"}
JUDGMENTS = {"是", "否", "说不好", "非职责"}

NONDUTY_MAX = 0.30      # 非职责率超过这个 -> 切块不可信
UNSURE_MAX = 0.50       # 说不好率超过这个 -> 职责描述空泛
MIN_DENOM = 3           # 有效分母下限

DOUBT_MIN_BLOCKS = 5    # 有效块数低于这个 -> 分数存疑
DOUBT_PARTIAL = 0.50    # 「部分有」占比超过这个 -> 分数存疑


def decide(blocks, reliable, lo=0.50, hi=0.65):
    """从块标签算岗位结论。返回 (verdict, ratio_str, why)。

    顺序有意义，命中即停：先排除「这条数据不可信」，再谈占比。
    """
    duty = [b for b in blocks if b.get("j")]
    if not reliable or not duty:
        return "需整篇判定", "-", "切块不可靠"
    c = Counter(b["j"] for b in duty)
    n = len(duty)
    yes, no = c["是"], c["否"]
    if c["非职责"] / n > NONDUTY_MAX:
        return "需整篇判定", f"{yes}/{yes+no}", f"非职责率 {c['非职责']}/{n}，切块不可信"
    if c["说不好"] / n > UNSURE_MAX:
        return "需整篇判定", f"{yes}/{yes+no}", f"说不好 {c['说不好']}/{n}，职责描述空泛"
    if yes + no < MIN_DENOM:
        return "需整篇判定", f"{yes}/{yes+no}", f"有效分母 {yes+no} < {MIN_DENOM}"
    r = yes / (yes + no)
    v = "判否" if r < lo else ("保留·职责偏离" if r < hi else "保留")
    return v, f"{yes}/{yes+no}", f"族占比 {r:.0%}"


def match_score(blocks):
    """匹配分 = 100 × Σ(命中块权重) / Σ(计分块权重)。

    **分母跟着 JD 长度走，长度偏置自然消失**——这是换掉 v1「命中数」
    最主要的理由。没有门槛加减分，所以它是纯比率，天然落在 [0,100]。
    """
    den = sum(b["w"] for b in blocks)
    if den <= 0:
        return 0, False, "没有计分块"
    num = sum(b["w"] * taxonomy.HIT_VALUES.get(b.get("hit"), 0.0) for b in blocks)
    n_partial = sum(1 for b in blocks if b.get("hit") == "部分有")
    # 两档不三档：可操作的区分只有「这分数能信 / 不能信」。
    # 没人会因为一条是「中置信」就改变动作——三档是给开发者看的。
    doubt = len(blocks) < DOUBT_MIN_BLOCKS or n_partial / len(blocks) > DOUBT_PARTIAL
    why = ("有效块数 %d < %d" % (len(blocks), DOUBT_MIN_BLOCKS)
           if len(blocks) < DOUBT_MIN_BLOCKS
           else ("「部分有」%d/%d 过半" % (n_partial, len(blocks)) if doubt else ""))
    return round(100 * num / den), doubt, why


# ------------------------------------------------------------------ 收判读结果

def collect(jdir, expect):
    got, rows = [], []
    for n in range(1, expect + 1):
        p = os.path.join(jdir, f"judge_verdict_{n}.json")
        if not os.path.exists(p):
            continue
        data = load_json(p, [])
        rows += data.get("items", data) if isinstance(data, dict) else data
        got.append(n)
    return got, rows


def archive(cfg, jdir, got):
    """吃掉的批次挪出 judge/，否则下次 judge_task.py 会把它们当成
    「还没消费的判读结果」拦住。挪走而不是删掉——判读结果是这条链路上
    最贵的东西，出了问题要能翻回去看原始标签。"""
    cdir = os.path.join(out_dir(cfg, "judge_consumed"),
                        datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(cdir, exist_ok=True)
    for n in got:
        os.replace(os.path.join(jdir, f"judge_verdict_{n}.json"),
                   os.path.join(cdir, f"judge_verdict_{n}.json"))
    info(f"已消费的 {len(got)} 个批次挪到 {cdir}")


# ------------------------------------------------------------------ Pass A

def apply_annotate(cfg, src, rows, tx, tx_path):
    problems = []
    # judged.json 是跨轮累积的缓存，永远是「读出来再合并」，绝不重建。
    # 重建一次就等于把之前所有轮花掉的判读额度全扔了。
    judged = load_json(out_dir(cfg, "judged.json"), {}) or {}
    new_points, dropped_pt = [], []

    for r in rows:
        jid = r.get("job_id")
        it = src.get(jid)
        if not it:
            problems.append(f"{r.get('company','?')}: job_id 不在任务包里")
            continue
        by_i = {b["i"]: b for b in it["blocks"]}
        got = r.get("blocks") or []
        if len(got) != len(it["blocks"]):
            problems.append(f"{it['company'][:12]}: 判了 {len(got)} 块，"
                            f"实际 {len(it['blocks'])} 块")

        merged = []
        for b in got:
            base = by_i.get(b.get("i"))
            if base is None:
                problems.append(f"{it['company'][:12]}: 块下标 {b.get('i')} 不存在")
                continue
            cat = b.get("cat")
            if cat not in taxonomy.CATEGORIES:
                problems.append(f"{it['company'][:12]}: 类目「{cat}」不在固定表里")
            pt = b.get("pt")
            # 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
            counts, _w = taxonomy.CATEGORIES.get(cat, (False, 0.0))
            if pt and not counts:
                dropped_pt.append((cat, pt))
                pt = None
            rec = {"i": base["i"], "sec": base["sec"], "t": base["t"],
                   "cat": cat, "pt": pt}
            if base["sec"] == "职责":
                if b.get("j") not in JUDGMENTS:
                    problems.append(f"{it['company'][:12]}: 主判据「{b.get('j')}」不合法")
                if b.get("f") not in FAMILIES:
                    problems.append(f"{it['company'][:12]}: 族标签「{b.get('f')}」不在固定表里")
                rec["j"], rec["f"] = b.get("j"), b.get("f")
            elif b.get("j") or b.get("f"):
                problems.append(f"{it['company'][:12]}: 非职责段的块不该有 j/f")
            merged.append(rec)

        tracks = [t for t in (r.get("tracks") or [])]
        tags = [t for t in (r.get("tags") or [])
                if t in taxonomy.ANTI_BARRIERS or t in taxonomy.GROWTH_TAGS]
        bad_tags = set(r.get("tags") or []) - set(tags)
        if bad_tags:
            problems.append(f"{it['company'][:12]}: tag {sorted(bad_tags)} 不在固定表里")

        v, ratio, why = decide(merged, it["reliable"])
        judged[jid] = {
            "h": it["h"], "reliable": it["reliable"],
            "verdict": v, "ratio": ratio, "why": why,
            "families": dict(Counter(b["f"] for b in merged
                                     if b.get("j") == "是" and b.get("f")).most_common()),
            "tracks": tracks, "tags": tags, "blocks": merged,
        }
        new_points += taxonomy.observe(tx, merged, tracks)

    save_json(out_dir(cfg, "judged.json"), judged)
    tx["rounds"] = tx.get("rounds", 0) + 1
    taxonomy.save(tx_path, tx)
    info(f"本轮合并 {len(rows)} 条，累计 judged.json {len(judged)} 条")

    info("")
    info("=== 结论分布（重算后）===")
    for k, n in Counter(v["verdict"] for v in judged.values()).most_common():
        info(f"  {k:<14}{n:>4}")

    allb = [b for v in judged.values() for b in v["blocks"]]
    duty = [b for b in allb if b.get("j")]
    if duty:
        info("")
        info(f"=== 职责块主判据（共 {len(duty)} 块）===")
        for k, n in Counter(b["j"] for b in duty).most_common():
            info(f"  {k:<8}{n:>5}  {n*100//len(duty)}%")
        nd = sum(1 for b in duty if b["j"] == "非职责")
        info(f"非职责率 {nd}/{len(duty)} = {nd*100//len(duty)}%"
             f"  —— 这是**切块**的质量指标，不是岗位的属性。"
             f"超过 5% 就该回去看 chunker，而不是调阈值。")
    if allb:
        info("")
        info(f"=== 类目分布（共 {len(allb)} 块）===")
        for k, n in Counter(b.get("cat") for b in allb).most_common():
            counts, w = taxonomy.CATEGORIES.get(k, (False, 0))
            info(f"  {str(k):<10}{n:>5}  {n*100//len(allb):>3}%   "
                 f"{'进分母 w=' + str(w) if counts else '不进分母'}")

    if dropped_pt:
        from collections import Counter as _C
        c = _C(f"{k}·{v}" for k, v in dropped_pt)
        info("")
        info(f"丢弃 {len(dropped_pt)} 个不该有的要点（打在不进分母的类目上）：")
        info("  " + "   ".join(f"{k}({n})" for k, n in c.most_common(10)))
        info("  这些进了频率报告，就等于告诉使用者「这个方向的市场要求你会双休」。")

    info("")
    info(f"=== 要点表 ===")
    info(f"  累计 {len(tx['points'])} 个要点，本轮新增 {len(set(new_points))} 个")
    conflicts = [p for p, r in tx["points"].items() if r.get("cat_conflicts")]
    if conflicts:
        info(f"  {len(conflicts)} 个要点被打成过不同类目（要点太宽泛，该拆）："
             f"{conflicts[:6]}")
    due, why = taxonomy.review_due(tx)
    if due:
        warn(f"要点表该做人工查验了：{why}")
        warn(f"  跑 python report_freq.py --review 出查验报告。"
             f"不合并同义项，频率表会被打散，统计失真。")
    return problems


# ------------------------------------------------------------------ Pass B

def apply_match(cfg, src, rows):
    problems = []
    matched = load_json(out_dir(cfg, "matched.json"), {}) or {}

    for r in rows:
        jid = r.get("job_id")
        it = src.get(jid)
        if not it:
            problems.append(f"{r.get('job_id','?')}: job_id 不在任务包里")
            continue
        by_i = {b["i"]: b for b in it["blocks"]}
        got = r.get("blocks") or []
        if len(got) != len(it["blocks"]):
            problems.append(f"{it['company'][:12]}: 判了 {len(got)} 块，"
                            f"实际 {len(it['blocks'])} 块")

        merged = []
        for b in got:
            base = by_i.get(b.get("i"))
            if base is None:
                problems.append(f"{it['company'][:12]}: 块下标 {b.get('i')} 不存在")
                continue
            hit, ev = b.get("hit"), b.get("ev")
            if hit not in taxonomy.HIT_VALUES:
                problems.append(f"{it['company'][:12]}: 命中值「{hit}」不合法")
                hit = "没有"
            # 「必须给出对应的经历，给不出就是没有」——这条防的是判读方自说自话。
            if hit in ("有", "部分有") and not ev:
                problems.append(f"{it['company'][:12]}: 判「{hit}」但没给对应经历，按「没有」算")
                hit, ev = "没有", None
            merged.append({**base, "hit": hit, "ev": ev})

        score, doubt, why = match_score(merged)
        matched[jid] = {"h": it["h"], "score": score, "doubt": doubt,
                        "doubt_why": why, "blocks": merged}

    save_json(out_dir(cfg, "matched.json"), matched)
    info(f"本轮合并 {len(rows)} 条，累计 matched.json {len(matched)} 条")

    if matched:
        scores = sorted((v["score"] for v in matched.values()), reverse=True)
        nd = sum(1 for v in matched.values() if v["doubt"])
        info("")
        info(f"=== 匹配分（{len(scores)} 条）===")
        info(f"  最高 {scores[0]}  中位 {scores[len(scores)//2]}  最低 {scores[-1]}")
        info(f"  存疑 {nd} 条（{nd*100//len(scores)}%）—— 只标虚线下划线，不改分数")
        allb = [b for v in matched.values() for b in v["blocks"]]
        c = Counter(b["hit"] for b in allb)
        info(f"  块级命中: " + "  ".join(f"{k} {v}（{v*100//len(allb)}%）"
                                        for k, v in c.most_common()))
    return problems


# ------------------------------------------------------------------ 主流程

def main():
    ap = argparse.ArgumentParser(description="合并判读结果并重算规则")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    ap.add_argument("--pass", dest="pass_", choices=["annotate", "match"])
    ap.add_argument("--calibrate", action="store_true",
                    help="额外打印族占比阈值校准表（仅 annotate）")
    args = ap.parse_args()
    cfg = load_config(args.config, args.track)

    jdir = out_dir(cfg, "judge")
    manifest = load_json(os.path.join(jdir, "manifest.json"), None)
    if manifest is None:
        die(f"找不到 {jdir}/manifest.json。先跑 python judge_task.py。")
    pass_ = args.pass_ or manifest.get("pass", "annotate")
    if args.pass_ and manifest.get("pass") and args.pass_ != manifest["pass"]:
        die(f"任务包是 --pass {manifest['pass']} 生成的，你传的是 --pass {args.pass_}。"
            f"两趟的块含义不一样，混着用会算出没有意义的数。")

    src = {}
    for p in sorted(glob.glob(os.path.join(jdir, "judge_input_*.json"))):
        for it in (load_json(p, {}) or {}).get("items", []):
            src[it["job_id"]] = it

    expect = manifest.get("batches", 0)
    got, rows = collect(jdir, expect)
    missing = [n for n in range(1, expect + 1) if n not in got]
    if missing:
        warn(f"缺批次 {missing} / 共 {expect}。这些岗位这轮不会有结论，"
             f"下面的数只覆盖已完成的部分。重跑那几批即可，不用全部重来。")

    # 要点表跨方向共享——要点是 JD 的固有属性，跟你在找什么岗位无关。
    tx_path = resolve(cfg, cfg.get("taxonomy_path")) or shared_path(cfg, "taxonomy.json")
    tx = taxonomy.load(tx_path)

    if pass_ == "annotate":
        problems = apply_annotate(cfg, src, rows, tx, tx_path)
    else:
        problems = apply_match(cfg, src, rows)

    if got:
        archive(cfg, jdir, got)
    if got and not missing:
        # 批次收齐 = 本轮关闭。删掉 manifest 就是那个信号——
        # judge_task 靠它判断能不能安全地重建队列。
        os.remove(os.path.join(jdir, "manifest.json"))
        info("本轮批次已收齐，队列关闭（现在可以安全地生成下一轮任务包）")
    elif missing:
        info(f"本轮还有 {len(missing)} 批未收，队列保持开启。"
             f"只重跑缺的那几批，**不要**重新生成任务包——"
             f"那会在判读方脚底下把输入换掉。")

    if args.calibrate and pass_ == "annotate":
        judged = load_json(out_dir(cfg, "judged.json"), {}) or {}
        info("")
        info("=== 族占比阈值校准 ===")
        info("  阈值   判否  偏离  保留")
        for lo in (0.40, 0.50, 0.60, 0.65):
            c = Counter(decide(v["blocks"], v["reliable"], lo=lo)[0]
                        for v in judged.values())
            info(f"  {lo:.2f}  {c['判否']:>4}  {c['保留·职责偏离']:>4}  {c['保留']:>4}")

    if problems:
        info("")
        info(f"=== 判读质量问题（{len(problems)} 处）===")
        for p in problems[:25]:
            info(f"  - {p}")
        if len(problems) > 25:
            info(f"  … 还有 {len(problems)-25} 处")

    info("")
    if pass_ == "annotate":
        info("→ 下一步: python judge_task.py --pass match")
    else:
        info("→ 下一步: python pipeline.py --phase apply")


if __name__ == "__main__":
    main()
