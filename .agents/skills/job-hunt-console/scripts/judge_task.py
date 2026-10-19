"""生成判读任务包。这是「脚本 -> 模型」那一侧的边界。

    python judge_task.py --pass annotate    # Pass A：标注全部块
    python judge_task.py --pass match       # Pass B：块 vs 我的经历

脚本只做确定性的事：标题闸门、切块、算缓存键、分批。
**判读本身不在这里发生**，脚本里没有任何模型调用。

这个边界是**文件**，不是函数调用：脚本写出 judge_input_N.json，
运行时用 `config.judge.model` 指定的模型判读，结果写回 judge_verdict_N.json，
再由 judge_apply.py 消费。换运行时（Claude Code / Trae / WorkBuddy）
只需要换「谁来读 judge_input_N.json」，脚本一行都不用改。

模型名不写死在代码里，理由和 target_role 一样：换个人就不一样。

## 两趟为什么这么分

三类判断的作用域不同，混在一起会在做多方向（§3）时逼你重跑：

    类目 · 归一化要点 · 二级方向 · tag   只依赖 JD      -> 换方向换人都复用
    主判据 + 族标签                      依赖 target_role -> 换方向重判
    块匹配                               依赖 profile     -> 换人重判

职责块的类目和主判据在**同一趟**出（Pass A），省掉对三分之一的块读两遍。
"""
import argparse
import glob
import hashlib
import os
import shutil

import chunker
import taxonomy
from common import (die, info, load_config, load_json, out_dir, resolve,
                    save_json, shared_path, warn)

FAMILIES = ["产品", "开发", "设计", "运营", "数据", "测试", "销售", "职能", "其他"]
JUDGMENTS = ["是", "否", "说不好", "非职责"]

# 段名 -> 这一段的块默认属于哪个类目。模型可以改，但给个先验能显著减少乱标。
SECTION_HINT = {
    "职责": "职责", "要求": None, "加分": None, "反门槛": "反门槛",
    "排除": "自述排除", "福利": "福利", "公司": "公司介绍",
    "元信息": "元信息", "产品形态": "产品形态",
}


# ------------------------------------------------------------------ 标题闸门

def title_gate(jobs, cfg):
    """只判「明显不属于」，不判「是否属于」（spec §5）。

    只判断标题是否明确属于其他职业；目标职业由完整职责判读。
    标题带「工程师」，实际干的是产品经理的活。降级成「标题里有没有出现
    **其他职业的明确名词**」之后，误杀空间基本消失。

    宁可放过，不可误杀——放过的代价是多读一条 JD，误杀的代价是丢一个 offer。
    """
    g = cfg.get("title_gate", {})
    if not g.get("enabled", True):
        return jobs, []

    words = [w for w in g.get("exclude_titles", []) if w]
    # 排除词不能和目标岗位自己撞。给销售方向用这套时，「销售」是目标不是排除项；
    # 不设这道保险，配错一个词就会把整个方向静默清空。
    own = (cfg.get("target_role") or "") + "".join(cfg.get("role_aliases") or [])
    clash = [w for w in words if w and w in own]
    if clash:
        warn(f"标题闸门排除词 {clash} 出现在 target_role/role_aliases 里，已忽略这几个")
        words = [w for w in words if w not in clash]

    # 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
    protect = [w for w in g.get("protect_titles", []) if w]
    if not words:
        warn("title_gate.exclude_titles 是空的，闸门不生效——全部进入块级判定。"
             "能跑，只是更花判读额度。这两个列表都该在初始化访谈时按方向配。")
        return jobs, []
    if not protect:
        warn("title_gate.protect_titles 是空的。排除词在标题里可能是领域而不是职业"
             "（「客服产品实习生」的职业是产品），不填会误杀这类岗位。")

    kept, dropped, conflicts = [], [], []
    for j in jobs:
        title = chunker.normalize(j.get("title") or "")
        ex_hit = next((w for w in words if w in title), None)
        pr_hit = next((p for p in protect if p in title), None)
        if ex_hit and pr_hit:
            # 冲突必须留痕。protect 词配错一个（产品方向手滑填了「运营」），
            # 闸门就会静默失效，从输出上完全看不出来。
            conflicts.append((title, ex_hit, pr_hit))
            kept.append({**j, "_gate_conflict": f"含「{ex_hit}」但也含「{pr_hit}」，交给块级判定"})
        elif ex_hit:
            dropped.append({**j, "_gate_reason": f"标题含「{ex_hit}」"})
        else:
            kept.append(j)

    if conflicts:
        info(f"标题闸门冲突 {len(conflicts)} 条（排除词与保护词同时命中，已放行给块级判定）：")
        for t, e, p in conflicts[:8]:
            info(f"    {t[:30]:<32} 「{e}」vs「{p}」")
        if len(conflicts) > 8:
            info(f"    … 还有 {len(conflicts)-8} 条")
        if len(conflicts) > max(len(dropped), 1):
            warn("冲突比挡掉的还多——多半是某个保护词太宽，把闸门整个架空了。"
                 "回头核一下 protect_titles。")
    return kept, dropped


# ------------------------------------------------------------------ 缓存键

def _hash(parts):
    """缓存键。内容没变就不重判——这是重判成本能不能接受的分水岭。

    仅对内容或规则变化的输入重新判读。
    没有这个键，每改一次 chunker 就要把全库重判一遍。
    """
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def profile_hash(prof):
    """经历画像的指纹。画像改了，匹配结论就全部作废——匹配问的是
    「这一块和**我的**经历对不对得上」，我变了答案就变了。"""
    dims = prof.get("dimensions") or []
    return _hash([f"{d.get('name')}|{d.get('weight')}|{d.get('evidence')}"
                  for d in sorted(dims, key=lambda x: str(x.get("name")))])


# ------------------------------------------------------------------ Pass A 标注

def build_annotate(job):
    tags = chunker.job_tags(job)
    jd = job.get("jd") or ""
    blocks = chunker.chunk(jd, known_tags=tags)
    rel = chunker.reliability(jd, tags)
    return {
        "job_id": job["job_id"],
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "industry": job.get("company_industry", ""),
        "reliable": rel["reliable"],
        "h": _hash([f"{s}|{t}" for s, t in blocks]),
        "blocks": [{"i": i, "sec": s, "hint": SECTION_HINT.get(s), "t": t}
                   for i, (s, t) in enumerate(blocks)],
    }


def instruction_annotate(cfg, tx):
    role = cfg.get("target_role") or "目标岗位"
    known = sorted(tx.get("points", {}), key=lambda p: -tx["points"][p]["n"])
    return {
        "任务": "对每一个块打标；另外对每个岗位整体给二级方向和 tag。",
        "块级 · 类目": {
            "取值": list(taxonomy.CATEGORIES),
            "说明": "这套类目描述的是「招聘启事这种文体有哪些部分」，"
                    "不是「某个岗位需要什么」——所以它对任何求职方向都通用。",
            "提示": "输入里的 hint 是按段落小标题给的先验。它常常对，但**不是答案**："
                    "要求段里既有硬技能也有软技能也有专业背景。hint 为 null 就是没有先验。",
        },
        "块级 · 归一化要点": {
            "为什么": "同一能力要求可能有不同措辞；统计归一化要点，避免逐字统计把相同要求打散。",
            "怎么做": "把这一块讲的事压成一个 2-8 字的短语。"
                      "「熟练使用 SQL，能独立完成数据提取和基础分析」-> SQL；"
                      "「逻辑清晰，能把分析结论用简洁的方式表达出来」-> 逻辑表达。",
            "**优先复用下面已有的要点**": known[:300],
            "新建的条件": "已有表里确实没有对得上的才新建。"
                          "宁可复用一个稍宽的，也不要造 SQL能力 / 熟练SQL 这种同义项——"
                          "打散之后频率表就失真了。",
            "福利/公司介绍/元信息": "这三类不用给要点，填 null。",
        },
        "块级 · 仅职责块（sec = 职责）另加两个值": {
            "主判据": {
                "问题": f"一个「{role}」会不会做这件事？",
                "取值": {
                    "是": "会做", "否": "不会做，这是别的职能的活",
                    "说不好": "是活，但 JD 写得含糊，看不出属于哪个族",
                    "非职责": "压根不是活（「工作地点：苏州」）——说明切块切错了",
                },
                "注意": "「说不好」和「非职责」都不进分母，但**必须分开填**。"
                        "合在一起会让切块故障和真实模糊产生相同的信号，被静默吸收。",
            },
            "族标签": {
                "取值": FAMILIES,
                "判据": "看产出物是什么，不看用了什么工具。",
                "展开": [
                    "产出物是**可上线的业务功能**（业务代码、接口联调、重构、上线维护）-> 开发",
                    "产出物是**用来定义或验证产品的东西**（原型、工作流、Agent 配置、"
                    "Prompt、评测集、需求文档、竞品结论）-> 产品",
                ],
                "正确答案示例": {
                    "vibe coding 快速验证产品想法": "是·产品",
                    "用 Claude Code 生成前端代码作为高保真原型交付": "是·产品（产出物是原型）",
                    "基于 n8n 搭建工作流，AI 节点完成分类/抽取/质检": "是·产品（产出物是工作流）",
                    "参与后端接口开发与联调": "否·开发（产出物是可上线的业务功能）",
                    "整理会议纪要、安排会议室": "说不好·职能",
                },
                "为什么": "工具是谁都能用的，产出物才定义职能。"
                          "写死「写代码 = 否」会把最匹配的能力反而判成不匹配。",
            },
        },
        "岗位级 · 二级方向": {
            "轴固定，值开放": taxonomy.AXES,
            "多选": "一个岗位可以同时挂 AI + B端 + 中台。不是单选——"
                    "「AI 中台产品经理」同时是 AI、是中台。",
            "已见过的值": sorted(tx.get("tracks", {})),
            "判不出的轴": "整个轴留空，不要硬填。",
        },
        "岗位级 · tag": {
            "反门槛": {"取值": taxonomy.ANTI_BARRIERS,
                       "判据": "JD 白纸黑字写了才算。「985/211 优先」不是「不看院校」。"},
            "成长回报": {"取值": taxonomy.GROWTH_TAGS,
                         "判据": "同上，提不出就不给。宁可空着也不要凑。"},
        },
        "禁止": [
            "不要自己算族占比、匹配分，不要自己下岗位级保留/判否结论——"
            "那些规则是确定性的，由 judge_apply.py 执行。判读方既打标签又套规则，"
            "等于把可验证的一步也变成不可验证的。",
            "块数必须和输入的 blocks 数组一一对应，按 i 对齐，不合并、不跳过。",
        ],
        "输出格式": {
            "文件": "judge_verdict_<批次号>.json，和输入同目录",
            "内容": [{
                "job_id": "...", "company": "...",
                "blocks": [{"i": 0, "cat": "职责", "pt": "需求文档", "j": "是", "f": "产品"},
                           {"i": 9, "cat": "硬技能", "pt": "SQL"}],
                "tracks": ["AI", "B端"],
                "tags": ["不看专业", "有带教"],
            }],
            "说明": "cat=类目，pt=归一化要点，j=主判据，f=族标签。"
                    "j 和 f 只有职责块才有，其余块不要填。",
        },
    }


# ------------------------------------------------------------------ Pass B 匹配

def build_match(job, judged, ph):
    """只把**计分块**交出去。福利、公司介绍、反门槛这些不进匹配分母，
    连读都不用读——那是白花额度。"""
    v = judged.get(job["job_id"])
    if not v:
        return None
    scoring = []
    for b in v.get("blocks", []):
        counts, w = taxonomy.CATEGORIES.get(b.get("cat"), (False, 0.0))
        if not counts:
            continue
        if b.get("sec") == "加分":
            w *= taxonomy.BONUS_FACTOR
        scoring.append({"i": b["i"], "cat": b["cat"], "w": round(w, 2), "t": b["t"]})
    if not scoring:
        return None
    return {
        "job_id": job["job_id"],
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "h": _hash([v["h"], ph]),
        "blocks": scoring,
    }


def instruction_match(prof):
    dims = [{"维度": d.get("name"), "我的经历": d.get("evidence")}
            for d in (prof.get("dimensions") or []) if d.get("evidence")]
    return {
        "任务": "对每一个块，判断候选人的经历里有没有对得上的东西。",
        "候选人的经历": dims,
        "取值": {"有": "经历里有直接对应的", "部分有": "沾边但不完整", "没有": "没有"},
        "**必须给出对应的是哪一段经历**": "填 `ev` 字段，写上面「维度」里的名字。"
                                          "给不出对应经历的一律填「没有」——"
                                          "这条防的是判读方自说自话。",
        "部分有": "记 0.5 分。用在「做过指标体系但没搭过看板」这种沾边不完整的情况。",
        "不要做的": [
            "不要算匹配分——公式是确定性的，由 judge_apply.py 执行。",
            "不要因为块看起来不重要就跳过。块权重已经在输入的 w 字段里算好了，"
            "软技能自然会被压到 0.3，不需要你再压一次。",
        ],
        "输出格式": {
            "文件": "judge_verdict_<批次号>.json",
            "内容": [{"job_id": "...",
                     "blocks": [{"i": 0, "hit": "有", "ev": "PRD / 原型硬功"},
                                {"i": 3, "hit": "部分有", "ev": "数据分析基础"},
                                {"i": 5, "hit": "没有", "ev": None}]}],
        },
    }


# ------------------------------------------------------------------ 主流程

def batch(items, n_blocks, max_blocks, max_jobs):
    """按**块数配额**切批，不按岗位数。

    JD 长度差得太多（最短 3 块，最长 40+ 块），按岗位数切会出现
    一批 60 块、另一批 400 块，后者的判读质量会在后半段掉下去。
    """
    out, cur, load = [], [], 0
    for it in items:
        n = n_blocks(it)
        if cur and (load + n > max_blocks or len(cur) >= max_jobs):
            out.append(cur)
            cur, load = [], 0
        cur.append(it)
        load += n
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser(description="生成判读任务包")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    ap.add_argument("--pass", dest="pass_", choices=["annotate", "match"],
                    default="annotate")
    ap.add_argument("--force", action="store_true", help="忽略缓存，全部重判")
    ap.add_argument("--no-seed", action="store_true",
                    help="跳过冷启动种子轮，一次放出全部批次（要点表会分叉，不建议）")
    args = ap.parse_args()
    cfg = load_config(args.config, args.track)
    jcfg = cfg.get("judge", {})

    kept = load_json(out_dir(cfg, "stage0_kept.json"), None)
    if kept is None:
        die("找不到 stage0_kept.json。先跑 python pipeline.py --phase filter。")

    # 要点表跨方向共享——要点是 JD 的固有属性，跟你在找什么岗位无关。
    tx_path = resolve(cfg, cfg.get("taxonomy_path")) or shared_path(cfg, "taxonomy.json")
    tx = taxonomy.load(tx_path)

    if args.pass_ == "annotate":
        gated, dropped = title_gate(kept, cfg)
        save_json(out_dir(cfg, "_title_gated.json"), dropped)
        info(f"标题闸门: 挡掉 {len(dropped)} 条，剩 {len(gated)} 条"
             f"（只判「明显不属于」，已写入 _title_gated.json，未删除）")
        items = [build_annotate(j) for j in gated if j.get("job_id")]
        # 切不出任何块的（JD 是空的）不送判——送过去只能得到一个空结论，
        # 白占一个批次名额。它们不是被排除，只是没东西可读。
        empty = [i for i in items if not i["blocks"]]
        items = [i for i in items if i["blocks"]]
        if empty:
            save_json(out_dir(cfg, "_no_jd.json"),
                      [{"job_id": i["job_id"], "title": i["title"],
                        "company": i["company"]} for i in empty])
            info(f"{len(empty)} 条切不出任何块（JD 空），不送判"
                 f"（已写入 _no_jd.json；跑 enrich_company.py 补公司情报仍能给它们打分）")
        cache_file, instr = "judged.json", instruction_annotate(cfg, tx)
    else:
        judged = load_json(out_dir(cfg, "judged.json"), None)
        if judged is None:
            die("找不到 judged.json。匹配要按类目挑计分块，得先跑完 Pass A：\n"
                "  python judge_task.py --pass annotate")
        prof = load_json(cfg["scoring"]["profile_source"] if os.path.isabs(
            cfg["scoring"]["profile_source"]) else os.path.join(
            os.path.dirname(cfg["_config_path"]), cfg["scoring"]["profile_source"]), {})
        ph = profile_hash(prof)
        gated = {j["job_id"] for j in load_json(out_dir(cfg, "_title_gated.json"), []) or []}
        items = [x for x in (build_match(j, judged, ph)
                             for j in kept if j.get("job_id") not in gated) if x]
        cache_file, instr = "matched.json", instruction_match(prof)
        info(f"计分块共 {sum(len(i['blocks']) for i in items)} 块 "
             f"/ {len(items)} 条岗位（福利、公司介绍、反门槛等不进匹配分母，不送判）")

    done = {} if args.force else (load_json(out_dir(cfg, cache_file), {}) or {})
    todo = [it for it in items if done.get(it["job_id"], {}).get("h") != it["h"]]
    reused = len(items) - len(todo)

    jdir = out_dir(cfg, "judge")
    # 上一轮的批次文件必须清掉，否则新旧批次会混判。但判读结果是最贵的东西
    # （花了模型额度和时间），不能因为手滑重跑一次就没了。
    # 清掉之前要确认**本轮已经关闭**，否则会在判读方脚底下把队列换掉。
    #
    # 实际踩过：一个判读方正在判 1-2 批时，我重新生成了任务包，整个 judge/
    # 被清掉重建。它检测到输入变了、重读重判，没出错，但那两批白判了一遍。
    # 更糟的情况是它没检测到——那就会拿旧输入的标签去配新输入的块。
    #
    # judge_apply 收齐全部批次后会删掉 manifest.json，表示本轮关闭。
    # manifest 还在 = 本轮还开着 = 可能有判读方正在跑。
    stale = glob.glob(os.path.join(jdir, "judge_verdict_*.json"))
    open_round = os.path.exists(os.path.join(jdir, "manifest.json"))
    if (stale or open_round) and not args.force:
        die(f"{jdir} 里还有一轮没关闭"
            + (f"（{len(stale)} 个判读结果未消费）" if stale else "（判读可能还在进行）")
            + "。\n"
            "  收尾: python judge_apply.py   —— 批次收齐后会自动关闭本轮\n"
            "  缺批次就只重跑那几批，不要重新生成任务包\n"
            "  确实要丢弃整轮重来再加 --force")
    if os.path.isdir(jdir):
        shutil.rmtree(jdir)
    os.makedirs(jdir, exist_ok=True)

    max_blocks = int(jcfg.get("blocks_per_batch", 150))
    max_jobs = int(jcfg.get("batch_size", 20))
    batches = batch(todo, lambda x: len(x["blocks"]), max_blocks, max_jobs)

    # 冷启动种子轮。
    #
    # 已有要点表是在**生成任务包这一刻**烤进 manifest.json 的。
    # 要点表是空的时候把所有批次一次放出去并行判，「优先复用已有要点」
    # 这条约束等于没有——每个判读方各造各的词汇表，互相看不见，
    # 收敛不了不说，人工查验时要合并的是几百个同义项。
    #
    # 所以冷启动先只放 seed_batches 批。判完 judge_apply 会把要点写进
    # taxonomy.json，再跑一次 judge_task 时后面的批次就带着词汇表出去了。
    # 代价是多一个来回，换的是要点表从一开始就收敛。
    seed_n = int(jcfg.get("seed_batches", 3))
    seeding = (args.pass_ == "annotate" and not tx.get("points")
               and len(batches) > seed_n and not args.no_seed)
    if seeding:
        batches = batches[:seed_n]
    for n, b in enumerate(batches, 1):
        save_json(os.path.join(jdir, f"judge_input_{n}.json"),
                  {"batch": n, "of": len(batches), "pass": args.pass_, "items": b})

    model = (jcfg.get("model") or "").strip()
    save_json(os.path.join(jdir, "manifest.json"), {
        "pass": args.pass_, "model": model, "batches": len(batches),
        "blocks_per_batch": max_blocks, "to_judge": len(todo),
        "reused_from_cache": reused, "instruction": instr,
    })

    nb = sum(len(x["blocks"]) for x in todo)
    info(f"[{args.pass_}] 待判 {len(todo)} 条 / {nb} 块，沿用缓存 {reused} 条，"
         f"分 {len(batches)} 批（每批 <= {max_blocks} 块、<= {max_jobs} 条）")
    if seeding:
        info("")
        info(f"★ 冷启动种子轮：要点表是空的，这次只放 {seed_n} 批。")
        info(f"  判完跑 judge_apply.py，要点会写进 taxonomy.json；"
             f"再跑一次 judge_task.py，剩下的批次就带着词汇表出去，可以并行判。")
        info(f"  不这么做的话，并行判读方各造各的词汇表，"
             f"「优先复用已有要点」这条约束等于没有。")
    info("")
    if not model:
        warn("config.judge.model 是空的。判读模型必须由使用者选，脚本不替你决定。")
        warn("  跑 python init_config.py 补上，或直接编辑 config.json 的 judge.model。")
    else:
        info(f"→ 判读模型: {model}（来自 config.judge.model）")
    info(f"→ 任务包: {jdir}/judge_input_1..{len(batches)}.json")
    info(f"→ 判读说明: {jdir}/manifest.json 的 instruction 字段（逐字用，不要转述）")
    info(f"→ 每批各自写出 judge_verdict_N.json（一批一个文件，不要攒到最后一起写——"
         "中途断了就全丢了）")
    info(f"→ 判完跑: python judge_apply.py --pass {args.pass_}")


if __name__ == "__main__":
    main()
