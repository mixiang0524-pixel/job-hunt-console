"""按归一化要点生成类目化频率报告。

使用 --review 查看疑似同义项，交由人类决定合并。
统计归一化要点而非逐字相同的句子，避免同一要求被不同措辞打散。"""
import argparse
import csv
import os
from collections import Counter, defaultdict

import taxonomy
from common import die, info, load_config, load_json, out_dir, resolve, shared_path


def main():
    ap = argparse.ArgumentParser(description="类目化频率报告")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    ap.add_argument("--review", action="store_true", help="输出要点表人工查验清单")
    ap.add_argument("--top", type=int, default=12, help="每个类目显示前 N 个")
    args = ap.parse_args()
    cfg = load_config(args.config, args.track)

    judged = load_json(out_dir(cfg, "judged.json"), None)
    if judged is None:
        die("找不到 judged.json。先跑完 Pass A：python judge_task.py --pass annotate")

    # 只统计**进入结果**的岗位。把判否的算进来，报告回答的就不再是
    # 「我这个方向的市场要什么」，而是「我搜到的东西里有什么」。
    rows = load_json(out_dir(cfg, "rows.json"), []) or []
    ids = {r["job_id"] for r in rows} if rows else set(judged)
    if not rows:
        info("还没有 rows.json，按 judged.json 全量统计（含判否的岗位）")

    # 文档频次：一个要点在一条岗位里出现多次只算一次
    by_cat = defaultdict(Counter)
    n_jobs = 0
    for jid, v in judged.items():
        if jid not in ids:
            continue
        n_jobs += 1
        seen = set()
        for b in v.get("blocks", []):
            pt, cat = b.get("pt"), b.get("cat")
            if not pt or (cat, pt) in seen:
                continue
            seen.add((cat, pt))
            by_cat[cat][pt] += 1
    if not n_jobs:
        die("没有可统计的岗位")

    info(f"=== 类目化频率报告（{n_jobs} 条岗位，按文档频次）===")
    info("")
    for cat in taxonomy.CATEGORIES:
        c = by_cat.get(cat)
        if not c:
            continue
        counts, w = taxonomy.CATEGORIES[cat]
        head = f"【{cat}】" + ("" if counts else "（不进匹配分母）")
        items = "   ".join(f"{p} {n*100//n_jobs}%" for p, n in c.most_common(args.top))
        info(f"{head:<22}{items}")

    path = out_dir(cfg, "freq_by_category.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["category", "point", "jobs", "pct_of_jobs", "counts_toward_score"])
        for cat, c in by_cat.items():
            counts, _ = taxonomy.CATEGORIES.get(cat, (False, 0))
            for p, n in c.most_common():
                wr.writerow([cat, p, n, f"{n/n_jobs:.1%}", "Y" if counts else "N"])
    info("")
    info(f"完整表 -> {path}")

    # ---- 二级方向 ----
    tk = Counter(t for jid, v in judged.items() if jid in ids
                 for t in (v.get("tracks") or []))
    if tk:
        info("")
        info("=== 二级方向（多标签，一个岗位可挂多个）===")
        for axis, vals in taxonomy.AXES.items():
            line = "   ".join(f"{v} {tk[v]*100//n_jobs}%" for v in vals if tk[v])
            if line:
                info(f"  {axis}轴  {line}")
        extra = [t for t in tk if not any(t in v for v in taxonomy.AXES.values())]
        if extra:
            info(f"  轴外的新标签（该定期查验归轴）: "
                 + "   ".join(f"{t} {tk[t]}" for t in extra))

    # ---- 人工查验 ----
    # 要点表跨方向共享——要点是 JD 的固有属性，跟你在找什么岗位无关。
    tx_path = resolve(cfg, cfg.get("taxonomy_path")) or shared_path(cfg, "taxonomy.json")
    tx = taxonomy.load(tx_path)
    due, why = taxonomy.review_due(tx)

    if not args.review:
        if due:
            info("")
            info(f"⚠ 要点表该做人工查验了：{why}")
            info("  跑 python report_freq.py --review")
        return

    info("")
    info("=" * 62)
    info(f"要点表人工查验（{'已触发：' + why if due else '未到触发条件，手动查看'}）")
    info("=" * 62)
    pend = tx.get("pending_new") or []
    info(f"累计要点 {len(tx['points'])} 个，自上次查验后新增 {len(pend)} 个")
    if pend:
        info("")
        info("本次新增：")
        for i in range(0, len(pend), 6):
            info("  " + "   ".join(pend[i:i + 6]))

    pairs = taxonomy.similar_pairs(tx)
    if pairs:
        info("")
        info(f"疑似同义（{len(pairs)} 对）—— 合并哪一个由你定，脚本不替你合：")
        for a, b, why2 in pairs:
            na = tx["points"].get(a, {}).get("n", 0)
            nb = tx["points"].get(b, {}).get("n", 0)
            info(f"  {a}（{na}） ↔ {b}（{nb}）   {why2}")
    else:
        info("")
        info("没有发现疑似同义项。")

    conflicts = {p: r["cat_conflicts"] for p, r in tx["points"].items()
                 if r.get("cat_conflicts")}
    if conflicts:
        info("")
        info(f"被打成过多个类目的要点（{len(conflicts)} 个）—— 多半是要点太宽泛，该拆：")
        for p, cc in list(conflicts.items())[:15]:
            main_cat = tx["points"][p]["cat"]
            info(f"  {p}: 主 {main_cat} + " +
                 "  ".join(f"{k}×{v}" for k, v in cc.items()))

    info("")
    info("查验完请手动编辑 taxonomy.json：合并同义项（把要合掉的那条删掉，"
         "n 加到保留的那条上），然后把 pending_new 清空、rounds 归零。")
    info("不合并的后果不是报错，是频率表被静默打散——"
         "34% 变成四个 8%，而且从表面完全看不出来。")


if __name__ == "__main__":
    main()
