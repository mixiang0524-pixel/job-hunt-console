"""从模板搭 config.json / profile.json，并校验必填项。

这个脚本只搭骨架。真正的初始化访谈由 AI 完成（见 SKILL.md Step 0），
因为有些问题必须先读过简历才问得出来。

特别提醒：不要让使用者在空白页上凭空写种子关键词。
先抓 1-2 页样本 JD，从高频词里做候选表让人勾选——
空白页写出来的关键词不能用。
"""
import argparse
import json
import os
import shutil

from common import die, info, load_json, save_json, warn

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.normpath(os.path.join(HERE, "..", "templates"))

TIER_ADVICE = {
    "T0": ([], "无实习无完整项目：0-20 人公司是合理选择，默认全部展开"),
    "T1": ([], "有项目无实习：默认全部展开"),
    "T2": ([], "1 段实习：默认展开，开关随时可折叠 0-20 人"),
    "T3": (["0-20人"], "2 段以上实习：默认折叠 0-20 人"),
}

REQUIRED = [
    ("cities", "目标城市"),
    ("target_role", "目标岗位"),
    ("crawl.query", "搜索关键词"),
    ("crawl.scraper", "抓取脚本路径"),
    ("keywords.seed", "种子关键词"),
    ("keywords.core", "核心词（用于公司行业相关性打分与展示，不是闸门）"),
    ("title_gate.exclude_titles", "标题排除词（其他职业的明确名词）"),
    ("title_gate.protect_titles", "标题保护词（目标岗位自己的职业名词）"),
    ("judge.model", "块级判定用的模型"),
]

# 判读模型不给默认值，必须由使用者选。
# 这和 target_role 是同一类东西：换个人就不一样，写死在代码里就不可迁移了。
# 额度紧的人和追求准确率的人会做出不同的选择，我们没资格替他们定。

def dig(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def scaffold(dest, template, name):
    src = os.path.join(TPL, template)
    if not os.path.exists(src):
        die(f"找不到模板 {src}")
    target = os.path.join(dest, name)
    if os.path.exists(target):
        warn(f"{target} 已存在，跳过（不覆盖你已经填过的东西）")
        return target
    shutil.copyfile(src, target)
    info(f"已创建 {target}")
    return target


def validate(cfg_path, prof_path):
    cfg = load_json(cfg_path, {}) or {}
    prof = load_json(prof_path, {}) or {}
    problems = []

    for path, label in REQUIRED:
        v = dig(cfg, path)
        if not v:
            problems.append(f"config.{path} 未填 —— {label}")

    # 一个词同时在两个列表里是配置错误，不是「保护优先」这条规则能兜的
    ex = set(dig(cfg, "title_gate.exclude_titles") or [])
    pr = set(dig(cfg, "title_gate.protect_titles") or [])
    both = ex & pr
    if both:
        problems.append(f"title_gate 里 {sorted(both)} 同时出现在 exclude_titles "
                        f"和 protect_titles —— 这两个列表描述的是不同的职业，不该重叠")

    jm = dig(cfg, "judge.model")
    if jm and (not isinstance(jm, str) or not jm.strip()):
        problems.append("config.judge.model 必须是明确的模型标识")

    scraper = dig(cfg, "crawl.scraper")
    if scraper:
        p = scraper if os.path.isabs(scraper) else os.path.join(
            os.path.dirname(cfg_path), scraper)
        if not os.path.exists(p):
            problems.append(f"config.crawl.scraper 指向的文件不存在: {p}")

    evidence_mode = dig(cfg, "scoring.match_engine") == "evidence"
    if evidence_mode:
        info("经历匹配使用独立档案；运行 evidence_workflow.py init 后按任务包完善目录和访谈。")
    else:
        dims = prof.get("dimensions") or []
        if not dims:
            problems.append("profile.dimensions 是空的 —— 匹配分没有依据")
        for d in dims:
            if not d.get("evidence"):
                problems.append(f"维度「{d.get('name')}」没有 evidence —— 需要补充经历依据")

    tier = prof.get("experience_tier")
    if tier in TIER_ADVICE:
        folds, why = TIER_ADVICE[tier]
        cur = (cfg.get("view") or {}).get("fold_scales")
        if cur is None or cur != folds:
            info(f"建议：{why}")
            info(f"  -> config.view.fold_scales = {json.dumps(folds, ensure_ascii=False)}"
                 f"（这是界面折叠开关的初始值，随时可改，数据不会丢）")
    elif tier:
        warn(f"experience_tier 「{tier}」不是 T0/T1/T2/T3 之一")
    elif not evidence_mode:
        problems.append("profile.experience_tier 未填 —— 决定规模折叠开关的推荐默认值")

    if problems:
        info("")
        info("还需要补齐：")
        for p in problems:
            info(f"  - {p}")
        info("")
        info("这些不是可选项。缺了它们跑出来的排序没有意义。")
        return False

    info("配置校验通过。下一步: python crawl_once.py")
    return True


def main():
    ap = argparse.ArgumentParser(description="搭 config.json / profile.json 骨架")
    ap.add_argument("--dir", default=".", help="工作目录（默认当前目录）")
    ap.add_argument("--example", action="store_true",
                    help="用 AI 产品经理方向的实例做起点，而不是空白模板")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()

    dest = os.path.abspath(args.dir)
    os.makedirs(dest, exist_ok=True)
    cfg_path = os.path.join(dest, "config.json")
    prof_path = os.path.join(dest, "profile.json")

    if not args.validate_only:
        scaffold(dest, "config.ai-pm.json" if args.example else "config.blank.json",
                 "config.json")
        if args.example:
            scaffold(dest, "profile.example.json", "profile.json")
        else:
            from evidence_workflow import init_workspace
            from common import load_config
            init_workspace(load_config(cfg_path))
        info("")
        info("接下来按 SKILL.md Step 0 做初始化访谈，把上面两个文件填完。")
        info("")
        info("访谈时必须问到的一项 —— config.judge.model（块级判定用哪个模型）：")
        info("  填写宿主实际支持的模型标识；脚本不调用模型API。")
        info("  不要替使用者默认。额度紧的人和追求准确率的人会选不一样的。")
        info("")

    validate(cfg_path, prof_path)


if __name__ == "__main__":
    main()
