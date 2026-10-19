"""类目表、块权重、二级方向轴、tag 表，以及 taxonomy.json 的读写。

这里放的是**固定的**词表和**会增长的**词表之间的分界线：

  固定不增长   一级族（§7.2）、类目（§9）、二级方向的**轴**（§8.2）
  开放增长     归一化要点、二级方向的**标签值**

分界线的判据是「这个集合描述的东西会不会变」。
「公司里有哪些职能」几十年没变过，所以一级族固定。
「招聘启事这种文体有哪些部分」也不变，所以类目固定——
这也是类目能跨岗位方向通用的根本原因，它描述的不是「产品经理需要什么」。
而市场上会出新方向（当年的「增长」、现在的「Agent 编排」），所以标签值开放。

开放的那部分靠提示词约束收敛（「优先复用已有要点，实在没有才新建」），
**收敛不是保证**，所以必须有人工兜底，见 §12.3 和 review_due()。
"""
import json
import os
from datetime import date

# ---------------------------------------------------------------- 类目（固定）

# 类目 -> (进不进匹配分母, 块权重)
CATEGORIES = {
    "职责":     (True,  1.0),
    "硬技能":   (True,  1.0),
    # 软技能不是因为「每份 JD 都有」才降权。更硬的理由：
    # **面试之前你没有任何办法向对方证明你有这些软技能。**
    # 它在「我能不能拿到面试」这件事上几乎无效，权重就该低。
    "软技能":   (True,  0.3),
    "工具栈":   (True,  1.0),
    "赛道业务": (True,  1.0),
    "专业背景": (True,  1.0),
    "产品形态": (True,  1.0),
    "反门槛":   (False, 0.0),   # 转 tag，§11.2
    "自述排除": (False, 0.0),   # 负面信号
    "成长回报": (False, 0.0),   # 转 tag，§9.2
    "福利":     (False, 0.0),
    "公司介绍": (False, 0.0),
    "元信息":   (False, 0.0),
}

# 加分段的块权重再乘这个——加分项没满足不该按硬要求扣
BONUS_FACTOR = 0.5

# 命中档位 -> 得分。「部分有」记 0.5。
HIT_VALUES = {"有": 1.0, "部分有": 0.5, "没有": 0.0}

# ---------------------------------------------------------------- 方向轴（固定）

AXES = {
    "技术": ["AI", "大数据", "算法", "传统"],
    "用户": ["B端", "C端", "内部工具"],
    "职能": ["策略", "增长", "中台", "商业化", "供应链", "内容"],
}
# 轴固定，值开放：市场出新方向就该能加进来（§8.3）。
# 上面那些是**起始值**，不是全集。

# ---------------------------------------------------------------- tag（固定）

ANTI_BARRIERS = ["不看专业", "不看院校", "不要求经验", "面向本届"]
GROWTH_TAGS = ["有带教", "导师制", "接触真实数据", "跨团队协作",
               "明确转正路径", "有token/算力预算"]

# ---------------------------------------------------------------- 人工查验触发

REVIEW_ROUNDS = 3        # 每跑满 N 轮抓取
REVIEW_NEW_POINTS = 20   # 或要点表新增超过 N 条，以先到者为准


def blank():
    return {"points": {}, "tracks": {}, "rounds": 0,
            "last_review": None, "pending_new": []}


def load(path):
    if not os.path.exists(path):
        return blank()
    d = json.load(open(path, encoding="utf-8"))
    for k, v in blank().items():
        d.setdefault(k, v)
    return d


def save(path, tx):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(tx, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def observe(tx, blocks, tracks):
    """把这一轮见到的要点和方向标签记进表，返回本轮**新增**的要点列表。

    只记不判。同义合并是人的活——`SQL` / `sql能力` / `SQL技能` / `熟练SQL`
    机器分不清哪个该当主项，硬合并会把统计搅得更乱。
    """
    today = date.today().isoformat()
    new = []
    for b in blocks:
        pt, cat = b.get("pt"), b.get("cat")
        if not pt:
            continue
        rec = tx["points"].get(pt)
        if rec is None:
            tx["points"][pt] = {"cat": cat, "n": 1, "first_seen": today}
            new.append(pt)
        else:
            rec["n"] = rec.get("n", 0) + 1
            # 同一个要点被打成不同类目是**信号**，不是噪声：说明要点太宽泛
            # （「分析」既是硬技能又是职责），该拆。记下来给人工查验看。
            if rec.get("cat") != cat:
                rec.setdefault("cat_conflicts", {})
                rec["cat_conflicts"][cat] = rec["cat_conflicts"].get(cat, 0) + 1
    for t in tracks or []:
        axis = next((a for a, vs in AXES.items() if t in vs), "其他")
        rec = tx["tracks"].setdefault(t, {"axis": axis, "n": 0})
        rec["n"] += 1
    tx["pending_new"] = sorted(set(tx.get("pending_new", [])) | set(new))
    return new


def review_due(tx):
    """该不该做要点表人工查验。返回 (要不要, 原因)。

    §12.3 定的是硬性流程，不是建议：必须在报告里主动提醒，
    不能等使用者想起来——没人会主动想起来维护一张词表。
    """
    n = len(tx.get("pending_new", []))
    if n >= REVIEW_NEW_POINTS:
        return True, f"要点表新增 {n} 条（>= {REVIEW_NEW_POINTS}）"
    if tx.get("rounds", 0) >= REVIEW_ROUNDS:
        return True, f"已跑 {tx['rounds']} 轮抓取（>= {REVIEW_ROUNDS}）"
    return False, ""


def similar_pairs(tx, limit=40):
    """疑似同义的要点对，给人工查验用。

    **不做语义相似**——那需要模型，而这一步的目的恰恰是把决定权交回给人。
    只用三条纯字符串规则，每条都为一类实际观察到的分叉而设：

      归一后相同   `AI产品` / `AI产品经验`      —— 后缀是修饰词不是区分
      共同前缀     `AI工具使用` / `AI工具应用`   —— 同一件事换了个动词
      子串         `Excel` / `PPT/Excel`        —— 一个是另一个的扩写

    子串规则必须收紧，避免将一次出现的短词误当成其他概念的规范形式。
    而这些本来就是不同的要点。一个只出现一次的短词不是「规范形式」，
    是**离群值**，拿它去归并高频词是反的。所以要求两条：
    长度差 <= 3（真的只是扩写），且短的那个出现过不止一次。
    """
    def norm(s):
        s = s.strip().lower().replace(" ", "")
        for junk in ("能力", "技能", "熟练", "掌握", "精通", "了解", "基础",
                     "经验", "相关", "的"):
            s = s.replace(junk, "")
        return s

    pts = tx.get("points", {})
    n = lambda p: pts.get(p, {}).get("n", 0)
    keys = sorted(pts)
    out, seen = [], set()

    def add(a, b, why):
        k = tuple(sorted((a, b)))
        if a != b and k not in seen:
            seen.add(k)
            out.append((a, b, why))

    by_norm = {}
    for p in keys:
        by_norm.setdefault(norm(p), []).append(p)
    for _, g in sorted(by_norm.items()):
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                add(g[i], g[j], "归一后相同")

    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            # 共同前缀：`产品定义方案` / `产品定义构思` / `产品定义经验`
            k = 0
            while k < min(len(a), len(b)) and a[k] == b[k]:
                k += 1
            if k >= 3 and 0 < len(a) - k <= 3 and 0 < len(b) - k <= 3:
                add(a, b, f"共同前缀「{a[:k]}」")
                continue
            short, long_ = (a, b) if len(a) <= len(b) else (b, a)
            if short in long_ and len(long_) - len(short) <= 3 and n(short) > 1:
                add(a, b, "子串")

    # 高频的排前面：合并一个 n=10 的分叉，比合并两个 n=1 的值钱得多
    out.sort(key=lambda x: -(n(x[0]) + n(x[1])))
    return out[:limit]
