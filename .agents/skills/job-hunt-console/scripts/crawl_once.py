"""单轮抓取。跑完就退出，绝不循环重试。

抓取是两段，成本差两个数量级：
    列表  每页 sleep(2,4) + 页面加载   ->  3 城 x 10 页约 3-5 分钟
    详情  每条 10-25 秒                ->  450 条 3-4 小时

所以列表每轮全量重抓，详情只抓本地库里没有的 job_id。
这就是重复运行从几小时降到几分钟的全部原因。

『新岗位』靠和本地库做 job_id 差集判定，不是靠网站的日期筛选——
列表 API 不含任何时间戳。
"""
import argparse
import json
import os
import subprocess
import sys

from common import (die, info, load_config, load_json, now, out_dir, resolve,
                    save_json, today, warn, shared_path)

RISK_MARKERS = ("code 31", "code 37", "环境存在异常", "访问频繁", "操作过于频繁")
AUTH_MARKERS = ("DetailLoginRequiredError", "需要登录", "登录后查看", "登录失效")


def scraper_path(cfg):
    p = resolve(cfg, cfg.get("crawl", {}).get("scraper"))
    if not p or not os.path.exists(p):
        die("config.crawl.scraper 没有指向可用的抓取脚本。\n"
            "  它应该是 boss_cdp_raw.py 的路径（相对 config.json 解析）。")
    return p


def run(cmd, log_path):
    """跑一条抓取命令，边跑边落日志，返回 (returncode, 全文)。"""
    info(f"$ {' '.join(cmd)}")
    buf = []
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace",
                          bufsize=1) as p:
        for line in p.stdout:
            sys.stdout.write(line)
            buf.append(line)
        p.wait()
        rc = p.returncode
    text = "".join(buf)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n===== {now()} rc={rc} =====\n{' '.join(cmd)}\n{text}")
    return rc, text


def check_stop(text):
    """风控和登录失效必须立刻停，不允许再探。"""
    for m in RISK_MARKERS:
        if m in text:
            return "RISK", m
    for m in AUTH_MARKERS:
        if m in text:
            return "AUTH", m
    return None, None


def crawl_lists(cfg, log_path):
    """列表抓取。每轮全量重抓，便宜。"""
    crawl = cfg.get("crawl", {})
    script = scraper_path(cfg)
    query = crawl.get("query") or cfg.get("target_role") or ""
    if not query:
        die("config.crawl.query 为空，不知道搜什么。")

    all_jobs = []
    for city in cfg.get("cities", []):
        outp = out_dir(cfg, "raw", f"list_{city}.json")
        cmd = [sys.executable, script, "--keyword", query, "--city", city,
               "--pages", str(crawl.get("pages_per_city", 10)),
               "--no-detail", "--output", outp]
        for k, v in (crawl.get("filters") or {}).items():
            cmd += [f"--{k}", str(v)]

        rc, text = run(cmd, log_path)
        kind, marker = check_stop(text)
        if kind:
            die(f"{kind}: 命中「{marker}」。按铁律立即停止，禁止重试。\n"
                f"  RISK -> 等一段时间再说，不要连续试。\n"
                f"  AUTH -> 重新登录后再跑。")
        if rc != 0:
            warn(f"{city} 列表抓取返回码 {rc}，本轮按缺失处理，不重试。")

        data = load_json(outp, {}) or {}
        jobs = data.get("jobs", []) if isinstance(data, dict) else (data or [])
        info(f"  {city}: 列表 {len(jobs)} 条")
        for j in jobs:
            j["city"] = city
        all_jobs.extend(jobs)
    return all_jobs


def merge_store(cfg, listed):
    """把列表结果并进 store，维护 first_seen / last_seen / miss_streak。

    miss_streak 只用于界面上的『久未出现』提示徽章，
    绝不写入状态字段——见 spec 7.5。
    """
    path = shared_path(cfg, "store.json")
    store = load_json(path, {}) or {}
    d = today()

    seen_now = set()
    new_ids = []
    for j in listed:
        jid = j.get("job_id") or j.get("encrypt_job_id")
        if not jid:
            continue
        seen_now.add(jid)
        old = store.get(jid)
        if old:
            # 列表字段每轮刷新，但绝不用空值覆盖已有的 jd
            jd = old.get("jd")
            merged = {**old, **{k: v for k, v in j.items() if v not in (None, "")}}
            if jd and not merged.get("jd"):
                merged["jd"] = jd
            merged["last_seen"] = d
            merged["miss_streak"] = 0
            store[jid] = merged
        else:
            store[jid] = {**j, "job_id": jid, "first_seen": d, "last_seen": d,
                          "miss_streak": 0}
            new_ids.append(jid)

    gone = 0
    for jid, rec in store.items():
        if jid not in seen_now:
            rec["miss_streak"] = rec.get("miss_streak", 0) + 1
            gone += 1

    save_json(path, store)
    hint = cfg.get("crawl", {}).get("stale_hint_miss_streak", 2)
    stale = sum(1 for r in store.values() if r.get("miss_streak", 0) >= hint)
    info(f"库存 {len(store)} 条：本轮新增 {len(new_ids)}，"
         f"本轮未出现 {gone}（其中连续 >= {hint} 轮未出现 {stale} 条，仅提示不改状态）")
    return store, new_ids


def crawl_details(cfg, store, log_path, limit=None, only_ids=None):
    """详情抓取。只抓库里没有 jd 的，这是省时间的关键。"""
    need = [j for j in store.values()
            if not (j.get("jd") or "").strip()
            and (only_ids is None or j["job_id"] in only_ids)]
    if limit:
        need = need[:limit]

    if not need:
        info("没有需要抓详情的岗位，跳过。")
        return store, []

    est = len(need) * 17 / 60
    info(f"需要抓详情 {len(need)} 条，按 10-25 秒/条估计约 {est:.0f} 分钟")

    feed = out_dir(cfg, "raw", "detail_feed.json")
    save_json(feed, {"keyword": cfg.get("crawl", {}).get("query", ""),
                     "jobs": need, "total": len(need)})
    outp = out_dir(cfg, "raw", "details_round.json")

    cmd = [sys.executable, scraper_path(cfg), "--input", feed,
           "--detail", "--detail-output", outp]
    rc, text = run(cmd, log_path)
    kind, marker = check_stop(text)
    if kind:
        warn(f"{kind}: 命中「{marker}」。本轮详情中止，已抓到的部分保留，禁止重试。")

    details = load_json(outp, []) or []
    got = 0
    for dt in details:
        jid = dt.get("job_id")
        if jid in store and (dt.get("jd") or "").strip():
            store[jid].update({k: v for k, v in dt.items() if v not in (None, "")})
            store[jid]["detail_at"] = today()
            got += 1

    save_json(shared_path(cfg, "store.json"), store)
    missing = [j["job_id"] for j in need if not (store[j["job_id"]].get("jd") or "").strip()]
    info(f"详情：成功 {got}，缺失 {len(missing)}")
    return store, missing


def main():
    ap = argparse.ArgumentParser(description="单轮抓取，跑完即停")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    ap.add_argument("--lists-only", action="store_true", help="只抓列表，不抓详情")
    ap.add_argument("--max-details", type=int, help="本轮最多抓几条详情")
    ap.add_argument("--retry-ids", help="定向补抓：逗号分隔的 job_id，或 JSON 文件路径")
    args = ap.parse_args()

    cfg = load_config(args.config, args.track)
    log_path = out_dir(cfg, "runs", "crawl.log")

    only = None
    if args.retry_ids:
        if os.path.exists(args.retry_ids):
            data = load_json(args.retry_ids, [])
            only = set(data if isinstance(data, list) else data.get("ids", []))
        else:
            only = {x.strip() for x in args.retry_ids.split(",") if x.strip()}
        info(f"定向补抓模式：{len(only)} 个 job_id。不做全量重跑。")
        store = load_json(shared_path(cfg, "store.json"), {}) or {}
        store, missing = crawl_details(cfg, store, log_path, only_ids=only)
        save_json(out_dir(cfg, "last_round.json"),
                  {"at": now(), "mode": "retry", "missing": missing})
        info("本轮结束。跑 diagnose_gaps.py 看结果。")
        return

    listed = crawl_lists(cfg, log_path)
    store, new_ids = merge_store(cfg, listed)

    missing = []
    if not args.lists_only:
        if cfg.get("crawl", {}).get("incremental", True):
            info("增量模式：只抓本地库里没有 JD 的岗位")
        store, missing = crawl_details(cfg, store, log_path, limit=args.max_details)

    save_json(out_dir(cfg, "last_round.json"), {
        "at": now(),
        "mode": "full",
        "listed": len(listed),
        "new_ids": new_ids,
        "missing": missing,
        "target": len(cfg.get("cities", [])) * cfg.get("crawl", {}).get("pages_per_city", 10) * 15,
    })

    info("")
    info("本轮结束，脚本到此为止——不会自动重试。")
    info("→ 下一步: python diagnose_gaps.py")


if __name__ == "__main__":
    main()
