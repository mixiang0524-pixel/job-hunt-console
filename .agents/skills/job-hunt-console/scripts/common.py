"""共用工具：配置加载、原子写、关键词匹配、归一化。

所有脚本都从这里取路径，不允许出现硬编码的绝对路径。
"""
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime

# ---------------------------------------------------------------- 配置与路径

DEFAULT_CONFIG_NAME = "config.json"


def find_config(explicit=None):
    """按 显式参数 -> 环境变量 -> 当前目录 -> 上级目录 的顺序找 config.json。"""
    if explicit:
        if not os.path.exists(explicit):
            die(f"找不到配置文件: {explicit}")
        return os.path.abspath(explicit)

    env = os.environ.get("JOB_HUNT_CONFIG")
    if env and os.path.exists(env):
        return os.path.abspath(env)

    here = os.path.abspath(os.getcwd())
    for _ in range(4):
        cand = os.path.join(here, DEFAULT_CONFIG_NAME)
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent

    die("找不到 config.json。先跑 init_config.py，或用 --config 指定路径。")


def load_config(explicit=None, track=None):
    """读配置。给了 track 就把 tracks/<track>/track.json 叠在根 config.json 上。

    多方向不是「每个方向一份库」，是**同一个库的不同视图**（§3.2）：

        config.json / store.json / state.json / taxonomy.json / company_intel.json
            ^ 全部共享，放在根目录
        tracks/<name>/track.json + profile.json + out/
            ^ 只有搜索词、目标岗位、画像、结果是各自的

    能共享的根本原因是 `job_id = MD5(岗位链接)`——它来自 URL，
    **和你用什么关键词搜到它完全无关**。所以在产品经理台子里投过的那条，
    新建产品运营方向后还是同一个 job_id，直接就能读到「已投递」。
    不需要同步，因为从来没有过两份。
    """
    path = find_config(explicit)
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_config_path"] = path
    cfg["_base_dir"] = os.path.dirname(path)
    cfg["_track"] = None

    if track:
        tdir = os.path.join(cfg["_base_dir"], "tracks", track)
        tpath = os.path.join(tdir, "track.json")
        if not os.path.exists(tpath):
            die(f"找不到 {tpath}。新建方向: python init_config.py --track {track}")
        with open(tpath, encoding="utf-8") as f:
            cfg.update(json.load(f))
        cfg["_track"] = track
        cfg["_track_dir"] = tdir
        cfg["output_dir"] = os.path.join(tdir, "out")
        # profile 默认落在方向目录里——画像是「我拿什么去投这个方向」，
        # 同一个人投不同方向，强调的经历不一样。
        cfg.setdefault("scoring", {}).setdefault(
            "profile_source", os.path.join(tdir, "profile.json"))
    return cfg


# 这几个文件是**跨方向共享**的，永远落在 config.json 同级目录，
# 不进 output_dir。放错地方的后果是「投过的岗位在另一个方向里又出现一遍」。
SHARED_FILES = ("store.json", "state.json", "taxonomy.json", "company_intel.json")


def shared_path(cfg, name):
    """共享文件的位置。单方向和多方向拿到的是同一个路径。"""
    root = os.path.join(cfg["_base_dir"], name)
    if not os.path.exists(root):
        # 向后兼容：v1 把 store.json 放在 out/ 里。找得到就用，并提示迁移。
        legacy = os.path.join(
            cfg.get("output_dir") if os.path.isabs(cfg.get("output_dir") or "")
            else os.path.join(cfg["_base_dir"], cfg.get("output_dir") or "./out"), name)
        if os.path.exists(legacy):
            if name in ("store.json", "company_intel.json"):
                warn(f"{name} 还在 out/ 里。它是跨方向共享的，"
                     f"建议挪到 {root}（多方向时放错地方会让状态各存一份）")
            return legacy
    return root


def out_dir(cfg, *parts):
    """输出目录，相对 config.json 所在目录解析。"""
    d = cfg.get("output_dir") or "./out"
    if not os.path.isabs(d):
        d = os.path.join(cfg["_base_dir"], d)
    full = os.path.join(d, *parts)
    os.makedirs(os.path.dirname(full) if parts else full, exist_ok=True)
    return full


def resolve(cfg, path):
    """把配置里的相对路径解析成绝对路径。"""
    if not path:
        return None
    return path if os.path.isabs(path) else os.path.normpath(
        os.path.join(cfg["_base_dir"], path))


# ---------------------------------------------------------------- IO

def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        warn(f"读取失败 {path}: {e}")
        return default


def save_json(path, data):
    """原子写。先写 .tmp 再 replace，避免中断留下半个文件。

    这条不是洁癖：本项目曾因为循环里直接覆盖写，中断一次把 145 条详情
    打成了 9 条。任何会被反复覆盖的文件都必须走这里。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def today():
    return date.today().isoformat()


def now():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 输出

def info(msg):
    print(msg, flush=True)


def warn(msg):
    print(f"[警告] {msg}", file=sys.stderr, flush=True)


def die(msg, code=1):
    print(f"[错误] {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------- 归一化

_PAREN = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]")
_SPACE = re.compile(r"\s+")


def to_halfwidth(s):
    return unicodedata.normalize("NFKC", s or "")


def norm_company(name):
    """公司名归一化：全角转半角、去括号内容、去空白。

    刻意不剥 '有限公司'/'科技' 这类后缀——那样会把不同主体合并到一起，
    代价比少合并一行大得多。
    """
    s = to_halfwidth(name)
    s = _PAREN.sub("", s)
    return _SPACE.sub("", s).strip()


_TITLE_NOISE = re.compile(r"(急招|急聘|高薪|包住|双休|可实习|长期)")
_TITLE_TAIL = re.compile(r"(实习生|实习|岗|职位)$")


def norm_title(title):
    """岗位名归一化，只用于判定'完全一致'的自动合并。"""
    s = to_halfwidth(title)
    s = _PAREN.sub("", s)
    s = _TITLE_NOISE.sub("", s)
    s = _SPACE.sub("", s).strip()
    s = _TITLE_TAIL.sub("", s)
    return s.lower()


def city_of(job):
    loc = job.get("location") or ""
    return (loc.split("·")[0] or job.get("city") or "").strip()


def company_key(job):
    """公司主键：优先站点稳定 ID，退化到归一化公司名。"""
    bid = (job.get("encrypt_brand_id") or "").strip()
    if bid:
        return f"bid:{bid}"
    return "name:" + norm_company(job.get("company") or job.get("boss_name") or "")


def similarity(a, b):
    """字符级相似度，用于'疑似重复'标记。"""
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- 关键词匹配

def _is_latin(kw):
    return bool(re.fullmatch(r"[A-Za-z0-9 .+#/_-]+", kw or ""))


def _build_pattern(kw):
    esc = re.escape(kw)
    if _is_latin(kw):
        # 拉丁词必须加边界，否则 ai 会命中 detail / email / training
        return re.compile(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(esc)


class KeywordMatcher:
    """带上下文护栏的关键词匹配。

    护栏解决的是真实误报：'PS/AI' 里的 AI 是 Adobe Illustrator，不是人工智能。
    """

    WINDOW = 8

    def __init__(self, keywords, guards=None):
        self.keywords = list(dict.fromkeys(k for k in keywords if k))
        self.patterns = {k: _build_pattern(k) for k in self.keywords}
        self.guards = guards or {}

    def _guard_ok(self, kw, text, start, end):
        g = self.guards.get(kw)
        if not g:
            return True

        deny = g.get("deny_near") or []
        if deny:
            lo = max(0, start - self.WINDOW)
            hi = min(len(text), end + self.WINDOW)
            window = text[lo:hi]
            for d in deny:
                if _build_pattern(d).search(window):
                    return False

        req = g.get("require_after") or []
        if req:
            tail = text[end:end + self.WINDOW]
            if not any(_build_pattern(r).search(tail) for r in req):
                return False

        return True

    def spans(self, text):
        """返回所有通过护栏的命中区间 [(start, end, kw), ...]。"""
        text = text or ""
        out = []
        for kw, pat in self.patterns.items():
            for m in pat.finditer(text):
                if self._guard_ok(kw, text, m.start(), m.end()):
                    out.append((m.start(), m.end(), kw))
        return out

    def hits(self, text):
        """返回命中的关键词集合（去重，不计次数）。"""
        return {kw for _, _, kw in self.spans(text)}


def match_text(job):
    """参与关键词匹配的文本范围：标题 + JD + 技能标签 + 学历标签。"""
    return "\n".join(str(job.get(k) or "") for k in
                     ("title", "jd", "skills", "skill_tags", "job_labels", "tags"))


def jd_len(job):
    return len((job.get("jd") or "").strip())


def length_band(job, cfg):
    sc = cfg.get("scoring", {})
    short = sc.get("jd_short_threshold", 300)
    long_ = sc.get("jd_long_threshold", 600)
    n = jd_len(job)
    return "short" if n < short else ("medium" if n < long_ else "long")
