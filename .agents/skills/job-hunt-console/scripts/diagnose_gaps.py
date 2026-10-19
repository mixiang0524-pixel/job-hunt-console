"""缺失诊断 + 申请报告。跑完停下等人批准，脚本自己绝不发起重试。

不允许笼统报『失败』。每条缺失必须归到一个类别里，
因为『网络断了』和『岗位已下线』要采取的行动完全相反。
"""
import argparse
import os
import re

from common import info, load_config, load_json, now, out_dir, save_json, shared_path, warn

# 判据按优先级从严到宽排列：先命中的先算
RULES = [
    ("RISK", r"code\s*3[17]|环境存在异常|访问频繁|操作过于频繁", "风控拦截", False,
     "立即停，禁止任何重试"),
    ("AUTH", r"DetailLoginRequiredError|需要登录|登录后查看|登录失效", "登录失效", False,
     "停，需人工重新登录"),
    ("GONE", r"404|职位不存在|已下线|职位已关闭|该职位不存在", "岗位已下线", False,
     "永久缺失，不建议重试"),
    ("NETWORK", r"ECONNREFUSED|连接超时|Timeout|CDP.*断开|连接被拒绝|Connection reset",
     "网络/CDP 中断", True, "可重试，代价小"),
    ("EXTRACT", r"JD 过短|提取校验失败|导航页|正文为空|解析失败", "提取失败", True,
     "可重试 1 次"),
]

SEC_PER_ITEM = 17


def classify(job_id, job, log_text):
    """在日志里找这条岗位所在行的错误痕迹。找不到就是 UNKNOWN，不硬猜。

    窗口必须限制在命中所在的那一行（外加紧随其后的缩进续行）。
    早先按『命中位置往后 400 字符』取窗口，结果一行日志才 40 字，
    窗口直接吃进后面好几条岗位的记录——把 5 条 GONE 读成了 8 条，
    NETWORK 被邻行的『已下线』顶掉。诊断报错的代价是让人做错决定，
    比不诊断更糟。
    """
    needles = [n for n in (job_id, job.get("job_link"), job.get("encrypt_job_id"))
               if n]
    parts = []
    for n in needles:
        for m in re.finditer(re.escape(str(n)), log_text):
            start = log_text.rfind("\n", 0, m.start()) + 1
            end = log_text.find("\n", m.end())
            end = len(log_text) if end == -1 else end
            parts.append(log_text[start:end])
            # 紧随其后的缩进行视为同一条记录的续行
            while end < len(log_text):
                nxt = log_text.find("\n", end + 1)
                nxt = len(log_text) if nxt == -1 else nxt
                line = log_text[end + 1:nxt]
                if not line[:1].isspace():
                    break
                parts.append(line)
                end = nxt
    window = "\n".join(parts)
    if not window:
        return "UNKNOWN", "日志里找不到这条的处理记录"

    for code, pat, desc, _, _ in RULES:
        if re.search(pat, window, re.IGNORECASE):
            return code, desc
    return "UNKNOWN", "有处理记录但没有可识别的错误特征"


def main():
    ap = argparse.ArgumentParser(description="缺失诊断，跑完停下等批准")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    args = ap.parse_args()
    cfg = load_config(args.config, args.track)

    last = load_json(out_dir(cfg, "last_round.json"))
    if not last:
        warn("找不到 last_round.json，先跑 crawl_once.py。")
        return
    store = load_json(shared_path(cfg, "store.json"), {}) or {}
    log_path = out_dir(cfg, "runs", "crawl.log")
    log_text = ""
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log_text = f.read()[-2_000_000:]

    missing = last.get("missing") or []
    total = len(store)
    with_jd = sum(1 for j in store.values() if (j.get("jd") or "").strip())

    buckets = {}
    for jid in missing:
        code, desc = classify(jid, store.get(jid, {}), log_text)
        buckets.setdefault(code, {"desc": desc, "ids": []})["ids"].append(jid)

    retryable = []
    for code, pat, desc, can_retry, _ in RULES:
        if can_retry and code in buckets:
            retryable.extend(buckets[code]["ids"])
    if "UNKNOWN" in buckets:
        pass  # UNKNOWN 交人工判断，不自动列入可重试

    # ---------------------------------------------------------- 报告
    lines = []
    lines.append(f"本轮结果：库存 {total} 条，其中已有 JD {with_jd} 条，缺 {len(missing)} 条")
    if last.get("new_ids") is not None:
        lines.append(f"          本轮新增 {len(last['new_ids'])} 条")
    if not missing:
        lines.append("无缺失。不需要任何重试。")
    else:
        lines.append("缺失归类：")
        order = [r[0] for r in RULES] + ["UNKNOWN"]
        meta = {r[0]: (r[3], r[4]) for r in RULES}
        meta["UNKNOWN"] = (False, "交人工判断")
        for code in order:
            b = buckets.get(code)
            if not b:
                continue
            can, advice = meta[code]
            lines.append(f"  {code:<8} {len(b['ids']):>3} 条  {b['desc']}，{advice}")

        if retryable:
            est = len(retryable) * SEC_PER_ITEM / 60
            lines.append(f"预计补抓耗时：约 {est:.1f} 分钟"
                         f"（{len(retryable)} 条 x 10-25 秒 + 启动开销）")
            skip = len(missing) - len(retryable)
            lines.append(f"建议：只对 {len(retryable)} 条可重试项定向补抓"
                         + (f"；其余 {skip} 条直接放弃" if skip else ""))
        else:
            lines.append("建议：本轮缺失都不属于可重试类别，不要重试。")

        gone = len(buckets.get("GONE", {}).get("ids", []))
        if gone and missing and gone / len(missing) >= 0.5:
            reachable = total - gone
            lines.append("")
            lines.append(f"注意：缺失里 {gone}/{len(missing)} 是已下线。"
                         f"目标条数设定不现实，这个搜索条件下的真实上限约 {reachable} 条。"
                         f"不要反复尝试去凑一个达不到的数字。")

    for code in ("RISK", "AUTH"):
        if code in buckets:
            lines.insert(0, f"!! {code}：命中风控/登录失效，"
                            f"在处理好之前不要再发起任何抓取 !!")

    report = "\n".join(lines)
    info("")
    info("=" * 60)
    info(report)
    info("=" * 60)

    save_json(out_dir(cfg, "gaps.json"), {
        "at": now(), "missing": missing, "buckets": buckets,
        "retryable": retryable, "report": report,
    })
    rp = out_dir(cfg, "runs", f"{__import__('common').today()}.md")
    with open(rp, "a", encoding="utf-8") as f:
        f.write(f"\n## {now()}\n\n```\n{report}\n```\n")

    if retryable:
        info("")
        info("要补抓的话（需要你明确同意，脚本不会自己跑）：")
        info(f"  python crawl_once.py --retry-ids {out_dir(cfg, 'gaps_retry.json')}")
        save_json(out_dir(cfg, "gaps_retry.json"), retryable)
    info("")
    info("是否继续？(y/n) —— 由你决定，脚本到此结束。")


if __name__ == "__main__":
    main()
