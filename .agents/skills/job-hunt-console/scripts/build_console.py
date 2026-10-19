"""输出投递台 HTML。

页面是纯静态的：没有定时器、没有自动跳转、没有网络请求。
投递动作完全由人手动完成——脚本不代投，页面也不代投。

三条界面硬性约束在这里落地：
  1. 表头不用 position:sticky（外层有 overflow-x:auto，sticky 会失效并压住首行）
  2. 改状态/搜索/筛选只改 class，绝不重排 DOM（否则滚动位置会跳回顶部）
  3. 状态字段只能由人点击改写，任何自动逻辑都不许写它
"""
import argparse
import html
import json
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import taxonomy
from build_resume_editor import write_editor
from common import (info, load_config, load_json, now, out_dir, save_json, today)

STATES = ["待定", "不感兴趣", "已投递", "停止招聘"]
SCALE_BUCKETS = ["0-20人", "20-99人", "规模未知"]

TAG_RULES = [
    ("线下面试", r"线下面试|到岗面试|现场面试"),
    # §13.3：外包/驻场不再硬排除，只打 tag。对实习生而言，
    # 再一般的公司实习了总归能学到东西，投不投由使用者自己看。
    ("外包驻场", r"外包|驻场|劳务派遣|人力外包"),
    ("需要作品", r"作品集|论文|专利|软著|竞赛|开源|GitHub|作品"),
    ("可转正", r"转正|留用|优秀者可留"),
    ("远程可", r"远程|居家|线上办公|remote"),
]


def build_link(r):
    link = r.get("job_link") or ""
    if not link:
        return ""
    parts = []
    if r.get("lid"):
        parts.append("lid=" + quote(str(r["lid"])))
    if r.get("security_id"):
        parts.append("securityId=" + quote(str(r["security_id"])))
    if not parts:
        return link
    return link + ("&" if "?" in link else "?") + "&".join(parts)


def fallback_link(r):
    q = f"{r.get('title','')} {r.get('company','')}".strip()
    return "https://www.zhipin.com/web/geek/job?query=" + quote(q)


def scale_bucket(r):
    s = (r.get("company_scale") or "").strip()
    if not s:
        return "规模未知"
    for b in ("0-20人", "20-99人"):
        if b.replace("人", "") in s:
            return b
    return s


def detect_tags(r, cfg):
    import re
    text = f"{r.get('title','')}\n{r.get('jd','')}\n{r.get('job_labels','')}"
    tags = [name for name, pat in TAG_RULES if re.search(pat, text, re.IGNORECASE)]
    fv = r.get("family_verdict")
    if fv == "需整篇判定":
        tags.append("需整篇判定")
    elif fv == "保留·职责偏离":
        tags.append("职责偏离")
    if not r.get("company_queried") and r.get("length_band") == "short":
        tags.append("情报不足")
    if scale_bucket(r) == "规模未知":
        tags.append("规模未知")
    if r.get("similar_to"):
        tags.append("疑似重复")
    if r.get("first_seen") == today():
        tags.append("NEW")
    hint = cfg.get("crawl", {}).get("stale_hint_miss_streak", 2)
    if r.get("miss_streak", 0) >= hint:
        tags.append("久未出现")
    return tags


def days_since(iso):
    if not iso:
        return None
    try:
        return (date.today() - date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def esc(x):
    return html.escape(str(x if x is not None else ""))


def main():
    ap = argparse.ArgumentParser(description="输出投递台 HTML")
    ap.add_argument("--config")
    ap.add_argument("--track", help="方向名，对应 tracks/<name>/")
    ap.add_argument("--output", help="HTML 输出路径（默认 output_dir/投递台.html）")
    args = ap.parse_args()
    cfg = load_config(args.config, args.track)

    rows = load_json(out_dir(cfg, "scored.json"))
    if not rows:
        rows = load_json(out_dir(cfg, "rows.json"))
        if not rows:
            info("找不到 scored.json / rows.json，先跑 pipeline.py 和 score.py。")
            return
        info("警告：只有 rows.json，没有分数。先跑 score.py 会好得多。")

    excluded = load_json(out_dir(cfg, "_excluded.json"), []) or []
    last = load_json(out_dir(cfg, "last_round.json"), {}) or {}
    stale_days = days_since(last.get("at"))

    payload = []
    for r in rows:
        # 旧版关键词分不能冒充新版经历匹配率；只改变展示，不修改源数据。
        score = r.get("match_score")
        match_ready = (bool(r.get("match_n")) and isinstance(score, (int, float))
                       and 0 <= score <= 100 and r.get("match_max", 100) == 100)
        payload.append({
            "id": r.get("job_id"),
            "title": r.get("title"),
            "company": r.get("company") or r.get("boss_name"),
            "city": r.get("city"),
            "location": r.get("location"),
            "salary": r.get("salary"),
            "salary_variants": r.get("salary_variants") or [],
            "scale": r.get("company_scale") or "未知",
            "bucket": scale_bucket(r),
            "stage": r.get("company_stage"),
            "industry": r.get("company_industry"),
            "active": r.get("boss_active_status"),
            "hr_count": r.get("hr_count", 1),
            "hr_list": r.get("hr_list") or [],
            "match": r.get("match_score") if match_ready else None,
            "evidence_match": (r.get("evidence_match")
                               if r.get("match_engine") == "evidence" else None),
            "match_max": r.get("match_max", 100),
            "company_score": r.get("company_score"),
            "blend": r.get("blend_score") if match_ready else None,
            "queried": bool(r.get("company_queried")),
            "dims": r.get("match_dims") or [],
            "barriers": r.get("barriers") or [],
            "hits": r.get("_hits") or [],
            "points": r.get("points") or [],
            "tracks": r.get("tracks") or [],
            "doubt": bool(r.get("match_doubt")),
            "doubt_why": r.get("match_doubt_why") or "",
            "mhits": r.get("match_hits") or [],
            "mmiss": r.get("match_misses") or [],
            "mn": r.get("match_n", 0),
            "anti": r.get("anti_barrier_bonus", 0),
            "family_verdict": r.get("family_verdict") or "",
            "family_ratio": r.get("family_ratio") or "",
            "family_why": r.get("family_why") or "",
            "families": r.get("families") or {},
            "tags": detect_tags(r, cfg),
            "benefits": [t for t in (r.get("tags") or [])
                         if t in taxonomy.ANTI_BARRIERS + taxonomy.GROWTH_TAGS],
            "jd": r.get("jd") or "",
            "jd_len": r.get("jd_len", len(r.get("jd") or "")),
            "publish_time": r.get("publish_time") or "",
            "first_seen": r.get("first_seen") or "",
            "last_seen": r.get("last_seen") or "",
            "welfare": r.get("welfare") or "",
            "link": build_link(r),
            "fallback": fallback_link(r),
            "intel": r.get("company_intel_note") or "",
            "adjust": r.get("score_adjust_reason") or "",
        })

    fold_default = cfg.get("view", {}).get("fold_scales") or []
    cities = sorted({p["city"] for p in payload if p["city"]})

    ctx = {
        "rows": payload,
        "cities": cities,
        "axes": taxonomy.AXES,
        "track_counts": dict(Counter(t for p in payload for t in p["tracks"])),
        "fold_default": fold_default,
        "scale_buckets": SCALE_BUCKETS,
        "states": STATES,
        "today": today(),
        "generated": now(),
        "stale_days": stale_days,
        "last_crawl": (last.get("at") or "")[:10],
        "match_max": payload[0]["match_max"] if payload else 100,
        "excluded": [{"title": e.get("title"), "company": e.get("company"),
                      "reason": e.get("_excluded_reason", ""),
                      "link": e.get("job_link", "")} for e in excluded],
        "target_role": cfg.get("target_role", ""),
    }

    out = args.output or out_dir(cfg, "投递台.html")
    # 自包含输出，模板随 skill 搬迁；不加载外部字体、脚本或样式。
    template = Path(__file__).resolve().parent.parent / "templates" / "console.html"
    data = json.dumps(ctx, ensure_ascii=False).replace("<", "\\u003c")
    tpl = template.read_text(encoding="utf-8").replace("__DATA__", data)
    with open(out, "w", encoding="utf-8") as f:
        f.write(tpl)
    write_editor(cfg, Path(out).parent / "简历编辑器.html")

    tag_count = Counter(t for p in payload for t in p["tags"])
    info(f"投递台 -> {out}")
    info(f"  {len(payload)} 行（已去重），已排除 {len(excluded)} 条在底部折叠区")
    info(f"  标记分布: " + "  ".join(f"{k}={v}" for k, v in tag_count.most_common()))
    if stale_days is not None and stale_days > 3:
        info(f"  提醒：距上次抓取已 {stale_days} 天")


if __name__ == "__main__":
    main()
