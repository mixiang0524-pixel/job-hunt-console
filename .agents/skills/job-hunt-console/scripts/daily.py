"""日常运行入口：抓一轮 -> 诊断 -> 停下等人。

刻意不做定时任务、不常驻、不假设有一台 24 小时开着的机器。
需要能部署服务器才用得起来的工具，多数人根本跑不起来。
代替调度的是提醒——投递台顶部会显示「距上次抓取 N 天」。

跑完不会自动重试、不会自动投递，也不会自动往下走打分和建页面：
中间要人看一眼诊断报告再决定。
"""
import argparse
import os
import subprocess
import sys

from common import info, load_config, load_json, out_dir, resolve, warn

HERE = os.path.dirname(os.path.abspath(__file__))


def step(name, script, cfg_path, extra=None, track=None):
    info("")
    info("=" * 60)
    info(f"[{name}]")
    info("=" * 60)
    cmd = [sys.executable, "-X", "utf8", os.path.join(HERE, script)]
    if cfg_path:
        cmd += ["--config", cfg_path]
    # --track 必须往下传。不传的话子脚本会退回根目录跑，
    # 结果写进错误的 out/，而且不会报错——只是数对不上。
    if track:
        cmd += ["--track", track]
    cmd += extra or []
    rc = subprocess.call(cmd)
    if rc != 0:
        warn(f"{script} 返回码 {rc}，链路到此中止。")
    return rc


def rebuild_evidence(cfg, track):
    """推进到下一个需要语义判读的位置，不消费旧版未完成的匹配批次。"""
    cfg_path = cfg["_config_path"]
    manifest = load_json(out_dir(cfg, "judge", "manifest.json"))
    if manifest:
        if manifest.get("pass") != "annotate":
            warn("旧匹配任务仍在进行。请先处理该任务，或在独立输出目录使用经历匹配。")
            return 1
        count = manifest.get("batches", 0)
        if not count or not all(os.path.isfile(out_dir(cfg, "judge", f"judge_verdict_{i}.json"))
                                for i in range(1, count + 1)):
            info("职业标注尚未收齐；继续已有 judge 任务，完成后再 --rebuild。")
            return 0
        if step("套用职业标注", "judge_apply.py", cfg_path, ["--pass", "annotate"], track):
            return 1
        if step("岗位去重", "pipeline.py", cfg_path, ["--phase", "apply"], track):
            return 1

    if not os.path.isfile(out_dir(cfg, "rows.json")):
        if step("硬排除", "pipeline.py", cfg_path, ["--phase", "filter"], track):
            return 1
        return step("职业标注任务", "judge_task.py", cfg_path, ["--pass", "annotate"], track)

    if step("初始化经历工作区", "evidence_workflow.py", cfg_path, ["init"], track):
        return 1
    evidence = cfg.get("evidence", {})
    catalog = load_json(resolve(cfg, evidence.get("catalog_path", "./evidence/capabilities.json")), {})
    if not catalog.get("modules"):
        return step("市场能力目录任务", "evidence_workflow.py", cfg_path, ["market-task"], track)
    if step("岗位要求任务", "evidence_workflow.py", cfg_path, ["requirements-task"], track):
        return 1
    workspace = resolve(cfg, evidence.get("workspace_dir", "./evidence"))
    if os.path.isfile(os.path.join(workspace, "requirements", "manifest.json")):
        info("岗位要求任务已准备好。按 brief 判读、apply-requirements 后再 --rebuild。")
        return 0
    if step("经历匹配与公司评分", "score.py", cfg_path, None, track):
        return 1
    return step("生成投递台", "build_console.py", cfg_path, None, track)


def main():
    ap = argparse.ArgumentParser(description="抓一轮 -> 诊断 -> 停")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    ap.add_argument("--lists-only", action="store_true")
    ap.add_argument("--max-details", type=int)
    ap.add_argument("--rebuild", action="store_true",
                    help="跳过抓取，只用现有数据重跑筛选/打分/建页面")
    args = ap.parse_args()

    cfg = load_config(args.config, args.track)
    cfg_path = cfg["_config_path"]

    if not args.rebuild:
        extra = []
        if args.lists_only:
            extra.append("--lists-only")
        if args.max_details:
            extra += ["--max-details", str(args.max_details)]
        if step("1/2 单轮抓取", "crawl_once.py", cfg_path, extra, args.track) != 0:
            return
        step("2/2 缺失诊断", "diagnose_gaps.py", cfg_path, None, args.track)

        info("")
        info("抓取环节结束，脚本在这里停住。")
        info("看完上面的诊断报告后，按需要执行：")
        info("  补抓（只在你同意时）  python crawl_once.py --retry-ids out/gaps_retry.json")
        info("  继续处理             python daily.py --rebuild")
        return

    if cfg.get("scoring", {}).get("match_engine") == "evidence":
        raise SystemExit(rebuild_evidence(cfg, args.track))

    # 判读是这条链路上唯一非确定性的一步，脚本替不了。Phase 2 有**两趟**：
    #   annotate  全部块 -> 类目 + 要点（职责块另加主判据 + 族标签）
    #   match     计分块 × 我的经历 -> 有/部分有/没有
    # 所以 --rebuild 是个状态机：看当前停在哪一趟，把那一趟推进一格。
    T = args.track
    jdir = out_dir(cfg, "judge")

    def ready():
        m = load_json(os.path.join(jdir, "manifest.json"), None)
        if not m:
            return None, False
        n = m.get("batches", 0)
        return m, all(os.path.exists(os.path.join(jdir, f"judge_verdict_{i}.json"))
                      for i in range(1, n + 1))

    def pause(m):
        info("")
        info(f"停在块级判定（{m['pass']} 趟）。这一步必须由模型逐块读，脚本不能替。")
        info(f"  任务包和判读说明: {jdir}")
        info(f"  模型用 config.judge.model = "
             f"{m.get('model') or '（未配置！必须由使用者选）'}")
        info(f"  每批各写一个 judge_verdict_N.json，共 {m.get('batches')} 批")
        info("  判完再跑: python daily.py --rebuild" + (f" --track {T}" if T else ""))

    m, ok = ready()

    if m is None:
        if step("1/2 Stage0 硬排除", "pipeline.py", cfg_path, ["--phase", "filter"], T) != 0:
            return
        if step("2/2 生成标注任务包", "judge_task.py", cfg_path,
                ["--pass", "annotate"], T) != 0:
            return
        return pause(ready()[0])

    if not ok:
        return pause(m)

    if m["pass"] == "annotate":
        if step("1/2 套用标注结论", "judge_apply.py", cfg_path,
                ["--pass", "annotate"], T) != 0:
            return
        if step("2/2 生成匹配任务包", "judge_task.py", cfg_path,
                ["--pass", "match"], T) != 0:
            return
        return pause(ready()[0])

    if step("1/5 套用匹配结论", "judge_apply.py", cfg_path, ["--pass", "match"], T) != 0:
        return
    if step("2/5 去重", "pipeline.py", cfg_path, ["--phase", "apply"], T) != 0:
        return
    step("3/5 打分", "score.py", cfg_path, None, T)
    step("4/5 频率报告", "report_freq.py", cfg_path, None, T)
    step("5/5 建投递台", "build_console.py", cfg_path, None, T)

    intel_task = out_dir(cfg, "company_task.json")
    subprocess.call([sys.executable, "-X", "utf8", os.path.join(HERE, "enrich_company.py"),
                     "--config", cfg_path, "--emit-task"]
                    + (["--track", T] if T else []))
    if os.path.exists(intel_task):
        n = (load_json(intel_task, {}) or {}).get("count", 0)
        if n:
            info("")
            info(f"还有 {n} 家公司需要外部检索（JD 太短，光看 JD 会低估它们）：")
            info(f"  清单: {intel_task}")
            info(f"  查完写 company_result.json 后："
                 f"python enrich_company.py --merge && python score.py && python build_console.py")

    info("")
    info(f"投递台: {out_dir(cfg, '投递台.html')}")


if __name__ == "__main__":
    main()
