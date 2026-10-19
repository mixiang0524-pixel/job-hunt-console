"""JD 切块：先按小标题与版式分段，再按条目分块。

先归一兼容汉字和不可见字符；职责、要求和元信息分开处理。
无法可靠切段时保留不确定标记，不能靠职责比例直接判否。"""
import re
import unicodedata

# 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
COMPAT_CJK = re.compile(r"[⺀-⻿⼀-⿟豈-﫿]")
INVISIBLE = re.compile(r"[​-‏⁠﻿]")


def normalize(text):
    """把 JD 文本归一到可比较的形态。整条管道在读 JD 之前都该先过这一步。

    只归一「长得像汉字但不是汉字」的码位和不可见字符，
    **不动标点**——全角逗号句号是中文正文的一部分，NFKC 全跑一遍会把它们
    压成半角，切出来的块读起来就不像人写的了。
    """
    if not text:
        return ""
    t = INVISIBLE.sub("", text).replace("\xa0", " ")
    return COMPAT_CJK.sub(lambda m: unicodedata.normalize("NFKC", m.group()), t)

# 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
SECTION_PATTERNS = [
    ("排除", r"(?:这个)?岗位不适合(?:谁|的人)?|我们不招|什么样的人不适合|不适合(?:的)?人选"),
    ("反门槛", r"我们不在乎|我们不看重|不设(?:门槛|限制)|无需|不要求(?:你)?(?:有|是)"),
    # 元信息段排在「公司」前面：「岗位基本信息」不能被「岗位介绍」之类抢走
    ("元信息", r"岗位基本信息|职位基本信息|基本信息|岗位信息|职位信息|实习信息|"
               r"招聘信息|其他信息|补充信息|实习安排|实习详情"),
    ("加分", r"加分项?|以下(?:能力|条件)?加分|优先(?:考虑)?(?:条件)?|Bonus|Nice\s*to\s*have"),
    ("职责", r"岗位职责|职位描述|工作内容|职责描述|工作职责|主要职责|岗位描述|工作描述|"
             r"职位职责|实习职责|职责与要求|你将做什么|你可以做什么|你将参与|你会参与|"
             r"你要做(?:的事|什么)|工作方向|你会负责(?:什么)?|你将负责|你需要做|"
             r"工作任务|Responsibilities|What\s+you.?ll\s+do|实习内容|实习工作|"
             r"主要工作|核心职责|你会做什么|你要做什么|Job\s+[Dd]uties|工作职责与内容"),
    # 「我们希望你」后面不一定跟「具备/是」——「我们希望你熟悉大模型」也是要求段。
    ("要求", r"任职要求|职位要求|岗位要求|任职资格|人员要求|我们希望你(?:具备|是)?|希望你(?:具备|是)|"
             r"我们期待|你需要具备|能力要求|应聘要求|Requirements|Qualifications|"
             r"基本要求|基础要求|技能要求|专业要求|学历要求|硬性条件|技术能力|能力素养|"
             r"岗位条件要求|任职条件|期望的候选人|我们需要你具备|你需要什么|"
             r"What\s+we.?re\s+looking\s+for|我们在乎的是"),
    ("福利", r"福利(?:待遇)?|薪资待遇|我们提供|你能收获(?:什么)?|你将获得|实习收获|Benefits|We\s+offer"),
    ("公司", r"关于我们|公司介绍|团队介绍|岗位介绍|职位介绍|关于(?:这个)?岗位|"
             r"岗位亮点|我们是谁|关于团队|About\s+us|Why\s+Join\s+Us|About\s+\w+"),
]

# 项目符号。除了常见的圆点方块，还要收两个来自 Word/Wingdings 的怪东西：
#   「l  协助车载控制器开发」——Wingdings 的圆点粘贴过来就是字母 l 加两个空格
#   「＃协助珠宝设计师」——有人拿井号当符号用
# 卡「l 后面至少两个空格」是为了不误伤真以 l 开头的英文单词。
BULLET_CORE = (r"[#＃]{1,6}|[•·▪◆●○▶>*\-—–§✦✓✔]|l\s{2,}|\d+[\.、\)）]|[（(]\d+[）)]|"
               r"[一二三四五六七八九十]+[、\.\)）]")
BULLET = re.compile(r"^\s*(?:" + BULLET_CORE + r")\s*")

# 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
HEAD_JUNK = re.compile(r"^\s*(?:" + BULLET_CORE + r")?\s*")

MIN_LEN = 4
MAX_LEN = 120
# 不含汉字的碎片要更长才留——英文逗号是从句分隔，切出来的短片段全是噪声
LATIN_MIN_LEN = 24
CJK = re.compile(r"[一-鿿]")

# BOSS 会把岗位标签原样塞进 JD 正文开头（"C端产品" "AI产品" "社区产品"）。
# 它们不是公司介绍，是产品形态元数据，单独归一段，否则会污染默认段。
FORM_TAG = re.compile(
    r"^(?:[CBcb]端|AI|大?数据|社区|社交|内容|电商|金融|云计算|网络安全|智能硬件|"
    r"用户/功能|平台|工具|企业服务|教育|医疗|游戏|广告|供应链|支付|SaaS)"
    r"[\w/]*产品$")

# 段落括号后缀：「岗位职责（需求定义）：」里的括号要先剥掉才认得出标题
PAREN_SUFFIX = re.compile(r"[（(][^）)]{0,20}[）)]\s*$")

# 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
BRACKET_HEAD = re.compile(r"^[【\[〖]([^】\]〗]{1,24})[】\]〗]$")

# 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
MD_HEAD = re.compile(r"^#{1,6}\s+\S")

# 中英双语标题的英文尾巴：「工作职责 RESPONSIBILITIES」「任职要求 REQUIREMENTS」。
# 必须卡「前面是汉字」，否则「About us」会被剥成「About」。
BILINGUAL_TAIL = re.compile(r"(?<=[一-鿿])\s*[A-Za-z][A-Za-z\s/&'’-]*$")

# 行内元信息：「工作地点：苏州」「实习时间：3个月」。
# 这类行不切换段——它夹在职责条目中间是常事，切换段会把后面的真职责一起带走。
# 它们只是自己单独成一块，标成元信息，从此不进任何判定。
#
# 故意不收「学历要求 / 专业要求」：那是真要求，Phase 2 要拿来判反门槛的，
# 归成元信息就把信号丢了。
META_LINE = re.compile(
    r"^\s*(?:工作地点|办公地点|上班地点|实习地点|工作城市|工作时间|上班时间|"
    r"实习时间|实习周期|实习时长|实习期限|实习地址|到岗时间|入职时间|"
    r"出勤要求|每周到岗|每周出勤|考勤|薪资|薪酬|日薪|月薪|时薪|实习工资|实习补贴|"
    r"招聘人数|需求人数|汇报对象|直属上级|所属部门|部门|职位类别|工作性质|"
    r"[Bb]ase|办公方式)\s*[:：]")


# 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
META_VALUE_MAX = 25
META_FIELD = re.compile(r"[^：:]{2,10}[：:]\s*(.*)$", re.S)

# 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
STRONG_TAIL = [
    ("职责", r"岗位职责|职位职责|工作职责|主要职责|你将参与的工作|你会做什么|"
             r"你将做什么|你将负责|Job\s+[Dd]uties"),
    ("要求", r"任职要求|职位要求|岗位要求|任职资格|我们希望你具备|期望的候选人|"
             r"岗位条件要求|我们需要你具备"),
    ("加分", r"加分项"),
]


def _peel_glued_head(line):
    """把粘在行尾的小标题剥下来。返回 (正文, 段名) —— 没粘就返回 (原行, None)。"""
    for name, pat in STRONG_TAIL:
        m = re.search(r"(?:" + pat + r")\s*[:：]?\s*$", line)
        # start() 卡 6 是为了别动本来就是纯标题的行——那种走正常路径
        if m and m.start() > 6:
            return line[:m.start()].rstrip(" ，,、。；;"), name
    return line, None


def _is_meta(text):
    """这一块是元信息还是被写进元信息段的正文？看冒号后面的值有多长。"""
    m = META_FIELD.match(text)
    return len(m.group(1) if m else text) <= META_VALUE_MAX


def _strip_num(line):
    """剥掉行首的序号和项目符号，只为判断这行是不是小标题。"""
    return HEAD_JUNK.sub("", line, count=1).strip()


def _match_section(line):
    """这一行是不是段落小标题？返回 (段名, 标题后剩下的正文)。

    要认的写法：
        岗位职责               整行就是标题
        【岗位职责】            带装饰
        一、你将负责            中文序号
        -岗位职责              项目符号
        优先/加分项            斜杠并列
        岗位职责：负责xxx       标题和正文写在同一行
    """
    raw = _strip_num(line)
    bracketed = bool(BRACKET_HEAD.match(raw)) or bool(MD_HEAD.match(line.strip()))
    s = raw.strip("【】[]〖〗<>《》 \t")

    for name, pat in SECTION_PATTERNS:
        # 「标题 + 冒号 + 正文」写在同一行。**不能先按长度过滤**——
        # 「岗位职责：1.数据分析工作：……」整行 200 字，按长度一刀切就永远认不出来。
        # 中间允许一小段括号注释：「岗位职责（需求定义）：」
        m = re.match(r"\s*(?:" + pat + r")\s*[（(]?[^）)：:]{0,12}[）)]?\s*[:：]\s*",
                     s, re.I)
        if m:
            return name, s[m.end():].strip()

    # 「整行就是标题」才需要长度保护，否则整段正文会被误判成标题
    if len(s) > 40:
        return None, line
    head = PAREN_SUFFIX.sub("", s).strip()      # 剥掉「（需求定义）」这类后缀
    # 中英双语标题：「工作职责 RESPONSIBILITIES」。只在中文后面才剥英文尾巴，
    # 否则「About us」会被剥成「About」，纯英文标题反而认不出来了。
    head = BILINGUAL_TAIL.sub("", head).strip()
    # 「优先/加分项」这种斜杠并列，整体 fullmatch 不上，拆开逐段试
    cands = [head]
    if "/" in head or "／" in head:
        cands += [p.strip() for p in re.split(r"[/／]", head)]
    for cand in cands:
        if not cand:
            continue
        for name, pat in SECTION_PATTERNS:
            # 尾巴上容忍标点：「你会做什么？」「岗位职责~」都是标题，
            # 只差一个符号就认不出来，代价是整篇零标题、全篇退回兜底。
            if re.fullmatch(r"\s*(?:" + pat + r")\s*[:：;；、,，?？!！。~～\-—]*\s*", cand, re.I):
                return name, ""

    # 小标题被写在行尾：「关于dobidobi 你将负责」。只对不带符号的短行开这个口子，
    # 否则「负责梳理产品需求与工作内容」这种正常条目会被当成标题。
    if len(head) <= 20 and not BULLET.match(line):
        for name, pat in SECTION_PATTERNS:
            if re.search(r"(?:" + pat + r")\s*$", head, re.I):
                return name, ""

    if bracketed:
        # 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
        for name, pat in SECTION_PATTERNS:
            if re.search(pat, s, re.I):
                return name, ""
        return "其他", ""
    return None, line


def _split_items(text):
    """把一行拆成若干条。分号、句号、项目符号都算边界。"""
    out = []
    for piece in re.split(r"[；;]|(?<=[。！？])", text):
        piece = BULLET.sub("", piece or "").strip(" \t：:，,、.。")
        if not piece:
            continue
        if len(piece) > MAX_LEN:
            # 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
            parts = [p.strip(" ，,") for p in re.split(r"[，,]", piece)]
            parts = [p for p in parts
                     if len(p) >= (MIN_LEN if CJK.search(p) else LATIN_MIN_LEN)]
            out.extend(parts if len(parts) > 1 else [piece])
        elif len(piece) >= MIN_LEN:
            out.append(piece)
    return out


def chunk(jd, default_section="公司", known_tags=None):
    """切块。返回 [(段名, 块文本), ...]。

    默认段是「公司」而不是「职责」：JD 开头没有小标题时通常是公司介绍，
    把它误当职责会污染族占比。
    """
    tags = {normalize(t).strip() for t in (known_tags or []) if t and t.strip()}
    sec = default_section
    out = []
    seen_header = False
    for raw in normalize(jd).splitlines():
        line = raw.strip()
        if not line:
            continue
        # 站点会把岗位自带的标签原样塞进 JD 开头。有 known_tags 就按实际字段剔，
        # 比正则可靠得多——「数据分析能力好」这种技能标签正则是猜不出来的。
        if not seen_header and (line in tags or FORM_TAG.match(line)):
            out.append(("产品形态", line, False))
            continue
        name, rest = _match_section(line)
        if name:
            sec, seen_header = name, True
            if not rest:
                continue
            line = rest
        elif META_LINE.match(line) and _is_meta(line):
            # 单行元信息：只把这一行标成元信息，**不切换段**。
            # 「工作地点：苏州」夹在职责条目中间是常事，切换段会把后面的真职责一起带走。
            # 值太长的（「核心方向：……60字的活」）不算元信息，落回正常流程。
            out.append(("元信息", line, False))
            continue
        # 小标题粘在这一行的尾巴上时，先把正文发出去，再切段。
        # 顺序不能反：那段正文属于**上一段**，切完段再发就归错段了。
        line, glued = _peel_glued_head(line)
        bulleted = bool(BULLET.match(line))
        for item in _split_items(line):
            # 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
            out.append(("职责" if sec == "元信息" and not _is_meta(item) else sec,
                        item, bulleted))
        if glued:
            sec, seen_header = glued, True
    return _infer_duty(out)



# 「小节标题」：两级结构的 JD 被扁平化之后，一级标题会变成一个独立的职责块。
#     1、用户需求发现与场景拆解        <- 这是小节标题，不是一项工作
#     深入理解证券研究、投资决策……      <- 这才是
# 锐天投资那条 14 块里有 6 块是这种，非职责率 43%，整条被迫退回整篇判定。
#
# 判据是**结构**不是用词：带序号的短块，后面紧跟不带序号的正文。
# 真正的编号职责列表，后面跟的是下一个编号项，不是散文。
# 句读也是信号——「2.参与产品功能梳理、流程设计」带顿号，是正文不是标题。
SUBHEAD_MAX = 20
SENT_PUNCT = re.compile(r"[。，；、,;]")


def _is_subhead(text, bulleted, nxt):
    if not bulleted or len(text) > SUBHEAD_MAX or SENT_PUNCT.search(text):
        return False
    return nxt is not None and nxt[0] == "职责" and not nxt[2]


def _infer_duty(blocks):
    """没有职责小标题时，推断出职责段。返回 [(段名, 块文本), ...]。

    两条推断规则，都只在整篇都没有职责段时才启用，且**都要求带项目符号**：

    A. 首个「要求/加分」标题之前、带符号的条目就是职责——常见写法是开头直接列
       1./2./3. 的职责条目，然后才出现「岗位要求」，作者只是没写职责标题。
    B. 整篇一个小标题都没有时，带项目符号的条目就是职责。

    为什么两条都卡「带符号」：公司介绍和价值观是成段的散文，不打点。
    公司价值观等散文不能靠激进推断全部划入职责。
    宁可少推断退回整篇判定，也不能把公司介绍当成职责，那会直接毁掉族占比。
    """
    blocks = [(("小节标题" if s == "职责" and _is_subhead(
                    t, b, blocks[i + 1] if i + 1 < len(blocks) else None) else s), t, b)
               for i, (s, t, b) in enumerate(blocks)]
    plain = [(s, t) for s, t, _ in blocks]
    if any(sec == "职责" for sec, _ in plain):
        return plain

    cut = next((i for i, (sec, _) in enumerate(plain) if sec in ("要求", "加分")), None)
    if cut is not None:
        bulls = {i for i, (s, _, b) in enumerate(blocks) if b and s == "公司" and i < cut}
        if len(bulls) >= 2:
            return [("职责", t) if i in bulls else (s, t)
                    for i, (s, t) in enumerate(plain)]
        return plain

    # 规则 B：完全无标题
    if any(s not in ("公司", "产品形态", "元信息") for s, _ in plain):
        return plain
    bulls = {i for i, (s, _, b) in enumerate(blocks) if b and s == "公司"}
    if len(bulls) < 3:
        return plain
    return [("职责", t) if i in bulls else (s, t)
            for i, (s, t) in enumerate(plain)]


def sections(jd, known_tags=None):
    """按段聚合，返回 {段名: [块, ...]}。"""
    d = {}
    for sec, item in chunk(jd, known_tags=known_tags):
        d.setdefault(sec, []).append(item)
    return d


def job_tags(job):
    """从岗位记录里取出站点自带的标签，用于剔除被塞进 JD 正文的标签行。"""
    out = []
    for k in ("skills", "job_labels", "skill_tags", "tags"):
        v = job.get(k)
        if isinstance(v, str):
            out += [x.strip() for x in re.split(r"[|｜,，/]", v)]
        elif isinstance(v, list):
            out += [str(x).strip() for x in v]
    return [x for x in out if x]


def has_headers(jd):
    """这份 JD 有没有可识别的段落小标题。"""
    for raw in normalize(jd).splitlines():
        name, _ = _match_section(raw.strip())
        if name:
            return True
    return False


def duty_blocks(jd, known_tags=None):
    """职责块。Phase 1 只用这一个。

    切不出职责段时返回空列表——调用方必须据此退回整篇语义判定，
    不允许拿别的段冒充职责段。
    """
    return sections(jd, known_tags).get("职责", [])


def reliability(jd, known_tags=None):
    """切块可靠性。给调用方决定要不要退回整篇判定。"""
    secs = sections(jd, known_tags)
    duty = secs.get("职责", [])
    n = sum(len(v) for v in secs.values())
    # 依据标题、版式和文本结构区分职责、要求与元信息，避免用位置猜测职业。
    no_req = not (secs.get('要求') or secs.get('加分'))
    swallowed = n >= 12 and (no_req or len(duty) / n > 0.85)
    return {
        "has_headers": has_headers(jd),
        "sections": sorted(secs),
        "n_blocks": n,
        "n_duty": len(duty),
        "swallowed": swallowed,
        "reliable": bool(duty) and len(duty) >= 2 and not swallowed,
    }
