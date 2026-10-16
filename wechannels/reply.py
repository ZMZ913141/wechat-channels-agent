"""固定规则自动回复：私信 / 评论命中关键词 → 自动发送预先配置的固定回复。

核心目标：**只要有私信或评论，就有自动回复**。
- 规则持久化在 state/reply_rules.json，可在面板「自动回复」页维护
- 每条规则字段：
    keyword  触发关键词（消息【包含】即命中，不区分大小写）
    reply    要发送的固定回复内容
    scope    适用范围：dm(仅私信) / comment(仅评论) / both(两者通用)
    enabled  是否启用
    is_default  是否为「默认兜底」规则（关键词填 * 或空即视为兜底）
- 匹配逻辑（match_reply）：
    1. 先按顺序匹配「具体关键词」规则，命中第一条即返回；
    2. 若都不中，再使用同 scope 的「默认兜底」规则；
    → 只要配了默认兜底规则，任何私信/评论都会得到一个自动回复。
- 回复动作本身仍由 browser-use Agent 在视频号页面完成（找到会话/评论并发送），
  不调用任何第三方/AI 生成内容——回复内容就是你在规则里写死的文字。
"""
import json
import os
import re
import time
import uuid

from . import config

RULES_FILE = os.path.join(config.STATE_DIR, "reply_rules.json")

# 关键词填这些值（或留空）都视为「默认兜底」规则
_DEFAULT_KEYWORDS = {"*", "默认", "其他", "其它", "任意", "全部", "any", ""}

SCOPES = ("dm", "comment", "both")
SCOPE_LABEL = {"dm": "私信", "comment": "评论", "both": "私信+评论"}


def _load() -> list:
    if not os.path.exists(RULES_FILE):
        return []
    try:
        with open(RULES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(rules: list) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def _norm_scope(scope: str) -> str:
    s = (scope or "both").strip().lower()
    return s if s in SCOPES else "both"


def _norm_keyword(keyword: str) -> tuple:
    """返回 (keyword, is_default)。默认兜底规则的 keyword 统一存成 '*'。"""
    kw = (keyword or "").strip()
    if kw in _DEFAULT_KEYWORDS:
        return "*", True
    return kw, False


def _scope_ok(rule_scope: str, query_scope: str) -> bool:
    rs = _norm_scope(rule_scope)
    return rs == "both" or rs == query_scope


# 一个规则的关键词字段可写多个别名，用 , ，、/ | 或空格分隔
_SPLIT_RE = re.compile(r"[,，、/|;\s]+")


def _keywords(keyword: str) -> list:
    """把「价格,多少钱,怎么卖」拆成 ['价格','多少钱','怎么卖']。"""
    return [k.strip().lower() for k in _SPLIT_RE.split(keyword or "") if k.strip()]


def load_rules() -> list:
    return _load()


def add_rule(keyword: str, reply: str, enabled: bool = True, scope: str = "both") -> dict:
    rules = _load()
    kw, is_default = _norm_keyword(keyword)
    rule = {
        "id": uuid.uuid4().hex[:8],
        "keyword": kw,
        "reply": (reply or "").strip(),
        "scope": _norm_scope(scope),
        "enabled": bool(enabled),
        "is_default": is_default,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    rules.append(rule)
    _save(rules)
    return rule


def update_rule(rid: str, keyword=None, reply=None, scope=None, enabled=None) -> bool:
    rules = _load()
    for r in rules:
        if r.get("id") != rid:
            continue
        if keyword is not None:
            kw, is_default = _norm_keyword(keyword)
            r["keyword"] = kw
            r["is_default"] = is_default
        if reply is not None:
            r["reply"] = (reply or "").strip()
        if scope is not None:
            r["scope"] = _norm_scope(scope)
        if enabled is not None:
            r["enabled"] = bool(enabled)
        # 手动改过就打标记，之后「刷新预置文案」不会再覆盖它
        r["user_edited"] = True
        _save(rules)
        return True
    return False


def delete_rule(rid: str) -> bool:
    rules = _load()
    new = [r for r in rules if r.get("id") != rid]
    if len(new) == len(rules):
        return False
    _save(new)
    return True


def toggle_rule(rid: str) -> bool:
    rules = _load()
    for r in rules:
        if r.get("id") == rid:
            r["enabled"] = not r.get("enabled", True)
            _save(rules)
            return r["enabled"]
    return False


def clear_rules() -> int:
    """清空全部规则，返回删除条数。"""
    n = len(_load())
    _save([])
    return n


# ---------------------------------------------------------------------------
# 统一后缀：每条自动回复都会自动带上（用于引导客户留下联系方式）
# 存在 state/reply_settings.json，可在面板「自动回复」页直接改，改完立即生效
# ---------------------------------------------------------------------------
DEFAULT_SUFFIX = "方便的话你把联系方式发我（平台这边发号码容易被限制），我看到马上加你"

_SETTINGS_FILE = os.path.join(config.STATE_DIR, "reply_settings.json")


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_settings(d: dict) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def get_suffix() -> str:
    s = _load_settings()
    if "suffix" in s:
        return s.get("suffix") or ""
    return DEFAULT_SUFFIX


def suffix_enabled() -> bool:
    return bool(_load_settings().get("suffix_enabled", True))


def set_suffix(text: str, enabled: bool = True) -> None:
    s = _load_settings()
    s["suffix"] = (text or "").strip()
    s["suffix_enabled"] = bool(enabled)
    _save_settings(s)


# 命中这些片段说明该条回复本身已经在要联系方式了，不再重复追加后缀。
# 注意别放太宽泛的词（如「发我」——「把截图发我」也会被误判，导致该条漏加后缀）
_SUFFIX_DUP_MARKERS = ("联系方式", "号码", "加你", "加个微信")


def with_suffix(text: str) -> str:
    """给回复追加统一后缀；已包含同类引导时不重复加。"""
    t = (text or "").rstrip()
    if not t or not suffix_enabled():
        return t
    sfx = get_suffix().strip()
    if not sfx:
        return t
    if any(m in t for m in _SUFFIX_DUP_MARKERS):
        return t
    # 结尾不是标点时补一个句号，避免 emoji 后直接接句子
    if t[-1] not in "。！？～~.!?…":
        t += "。"
    return t + sfx


def match_reply(text: str, scope: str = "both") -> str | None:
    """返回命中的固定回复；未命中返回 None。

    scope：dm / comment / both —— 决定使用哪些规则（both 的规则对两类都生效）。
    具体关键词优先；都不中时回落到「默认兜底」规则。
    """
    q = _norm_scope(scope)
    t = (text or "").strip().lower()
    default_reply = None
    for r in _load():
        if not r.get("enabled", True):
            continue
        if not _scope_ok(r.get("scope", "both"), q):
            continue
        kw = (r.get("keyword") or "").strip().lower()
        if kw == "*" or r.get("is_default"):
            if default_reply is None and (r.get("reply") or "").strip():
                default_reply = r.get("reply")
            continue
        for k in _keywords(kw):
            if k in t:
                return with_suffix(r.get("reply") or "")
    # 未命中具体关键词 → 用默认兜底（有则必回）
    return with_suffix(default_reply) if default_reply else None


def has_default(scope: str) -> bool:
    """指定 scope 下是否存在启用中的「默认兜底」规则（有 → 任何消息都会回复）。"""
    q = _norm_scope(scope)
    for r in _load():
        if not r.get("enabled", True):
            continue
        if not _scope_ok(r.get("scope", "both"), q):
            continue
        kw = (r.get("keyword") or "").strip().lower()
        if (kw == "*" or r.get("is_default")) and (r.get("reply") or "").strip():
            return True
    return False


# ---------------------------------------------------------------------------
# 预置规则库（雅太社 · 珠珠 · 新中式女装源头供应链）
# 说明：以下文案可直接用，也可在面板里逐条修改；【】内为需你补充的信息。
# ---------------------------------------------------------------------------
PRESET_RULES = [
    # —— 默认兜底：任何私信 / 任何评论都会得到一条回复 ——
    {"keyword": "*", "scope": "dm", "reply":
        "姐妹好呀～我是雅太社的珠珠👋 咱家在广东揭阳做新中式女装源头供应链，"
        "款式版型面料一条龙，支持小单混批、没囤货压力。想看款、问价、要地址，"
        "直接跟我说，我亲自帮你配～"},
    {"keyword": "*", "scope": "comment", "reply":
        "谢谢姐妹～我是雅太社的珠珠，做新中式女装源头供应。"
        "想看款、问价、了解拿货，私信我哦～"},

    # —— 具体关键词 ——
    {"keyword": "你好,您好,在吗,在么,哈喽,hi,hello,nihao", "scope": "both", "reply":
        "你好呀～我是雅太社的珠珠。想看款、问价，还是了解拿货政策？"
        "直接说，我帮你看～"},
    {"keyword": "价格,多少钱,怎么卖,报价,价位,拿货价,批发价,贵不贵", "scope": "both", "reply":
        "咱家是源头供应链，价格随款式和数量走～你把喜欢的款截图发我，"
        "我直接给你报实在价，小单混批也有优惠。"},
    {"keyword": "拿货,批发,混批,起批,几件起,小单,囤货,一件代发", "scope": "both", "reply":
        "支持小单混批，没有囤货压力～款式、版型、面料一条龙，看得见摸得着。"
        "你说风格和数量，我按你的店给你配～"},
    {"keyword": "地址,位置,在哪,怎么去,导航,展厅,工厂,参观,高铁,开车,上门", "scope": "both", "reply":
        "展厅在广东揭阳（源头工厂展厅），开车、高铁都能到～评论区扣 666，"
        "我把定位私信发你；来了直接报我名字「珠珠」，我带你逛～"},
    {"keyword": "微信,联系方式,电话,加个,私聊,加你,号码,weixin,vx,威信,wx", "scope": "both", "reply":
        "平台这边发号码容易被限制～方便的话你把联系方式发我，我看到马上加你；"
        "或者直接说需求，我这边先帮你安排～"},
    # 合规做法：让客户把方式留给你（客户留＝合规；自己公开留＝有被限流/折叠风险）
    {"keyword": "留个方式,怎么联系你,我加你,你加我,方便联系,加微信,加个微信", "scope": "dm", "reply":
        "方便的话你把联系方式发我，我看到马上加你～"
        "或者直接告诉我你要什么款、什么码数，我先帮你安排上。"},
    {"keyword": "尺码,码数,多大,身高,体重,胖,瘦,穿多大,几码,size", "scope": "both", "reply":
        "尺码我按身高体重帮你推荐～你告诉我身高、体重和三围，"
        "我帮你选最合适的码，不用担心买错。"},
    {"keyword": "面料,材质,成分,真丝,桑蚕丝,羊毛,羊绒,提花,织锦,雪纺,舒服,舒适", "scope": "both", "reply":
        "咱家主打新中式，提花、织锦、雪纺、羊毛羊绒都有，款式版型面料一条龙，"
        "基本都能摸到实物。想看哪件的面料细节，我拍给你～"},
    {"keyword": "新款,上新,有什么款,都有什么,看看款,目录,图册,款式,样品", "scope": "both", "reply":
        "新款都在我的主页和视频里～你也可以直接说想走的风格（通勤/日常/宴会），"
        "我按你的需求挑几款发你。"},
    {"keyword": "代理,加盟,合作,代发,供货,直播,带货,开店,拿样", "scope": "both", "reply":
        "欢迎合作～咱家是揭阳新中式女装源头供应链，支持小单混批、一件代发。"
        "你把渠道情况发我，我按量给你合作价～"},
    {"keyword": "发货,快递,物流,包邮,几天到,多久到,运费", "scope": "both", "reply":
        "一般当天～次日发出，走快递或物流。运费看地址和数量，你把地址发我，我算给你～"},
    {"keyword": "退换,售后,质量,瑕疵,色差,坏了,退货,退款", "scope": "both", "reply":
        "做的是长久生意～收到货有问题的姐妹放心，第一时间找我珠珠，我帮你处理到底👌"},
    {"keyword": "直播,试穿,几点播,什么时候播,直播间,模特,上身", "scope": "both", "reply":
        "我一般在展厅直播、拍视频～想看哪件的上身效果，你留言告诉我，"
        "我直播时给你试穿看看。"},
    {"keyword": "谢谢,感谢,谢了,多谢,thanks,thank", "scope": "both", "reply":
        "客气啦～有任何问题随时找我，我是雅太社的珠珠，亲自帮你配货😊"},
    {"keyword": "好看,漂亮,喜欢,好美,棒,赞,心动,爱了", "scope": "both", "reply":
        "谢谢喜欢～这可是咱家新中式的招牌款之一。"
        "想要的话告诉我尺码和颜色，我帮你安排好～"},
    {"keyword": "666,扣666,位置发我,发位置", "scope": "both", "reply":
        "收到～定位我私信发你哈（评论区不好发位置）。"
        "来了直接报我名字「珠珠」，我亲自带你逛展厅、帮你配货！"},
    {"keyword": "珠珠,是珠珠吗,你本人,本人吗", "scope": "both", "reply":
        "是我本人呀～雅太社的珠珠，新中式女装源头供应链。"
        "有什么需要直接跟我说，我亲自帮你配货～"},
    {"keyword": "优惠,便宜,少点,打折,活动,划算,贵了", "scope": "both", "reply":
        "咱家走源头价，已经把中间差价省掉啦～量大或者混批都有优惠，"
        "你把要的款和数量发我，我给你算最划算的方案～"},
    {"keyword": "起球,掉色,褪色,缩水,变形,质量好吗,耐穿", "scope": "both", "reply":
        "放心，出货前都会质检；新中式面料手洗或轻柔模式更耐穿。"
        "真有问题随时找我珠珠，包你售后～"},
]


def refresh_presets() -> int:
    """把「你没手动改过」的规则刷新为最新预置文案，返回更新条数。

    手动编辑过的规则（user_edited=True）会被跳过，不会覆盖你的改动。
    """
    rules = _load()
    idx = {}
    for i, r in enumerate(rules):
        idx[((r.get("keyword") or "").strip(), _norm_scope(r.get("scope", "both")))] = i
    n = 0
    for p in PRESET_RULES:
        kw, _ = _norm_keyword(p["keyword"])
        sc = _norm_scope(p.get("scope", "both"))
        i = idx.get((kw, sc))
        if i is None:
            continue
        r = rules[i]
        if r.get("user_edited"):
            continue
        new_text = p["reply"].strip()
        if (r.get("reply") or "").strip() != new_text:
            r["reply"] = new_text
            n += 1
    if n:
        _save(rules)
    return n


def seed_rules() -> int:
    """导入预置规则：只添加「关键词+scope 组合尚不存在」的规则，不覆盖你改过的内容。

    返回本次新增的条数（重复点击不会重复添加）。
    """
    rules = _load()
    existing = {(r.get("keyword"), _norm_scope(r.get("scope", "both"))) for r in rules}
    added = 0
    for p in PRESET_RULES:
        kw, _ = _norm_keyword(p["keyword"])
        sc = _norm_scope(p.get("scope", "both"))
        if (kw, sc) in existing:
            continue
        rules.append({
            "id": uuid.uuid4().hex[:8],
            "keyword": kw,
            "reply": p["reply"].strip(),
            "scope": sc,
            "enabled": True,
            "is_default": kw == "*",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        existing.add((kw, sc))
        added += 1
    if added:
        _save(rules)
    return added


# 给模型的任务：找到指定会话并发送固定回复
REPLY_TASK = """打开 {url} 视频号助手，进入「私信」页面。
在会话列表中找到与「{sender}」的会话，点开它，在底部输入框中输入以下文字并发送：
---
{reply}
---
要求：
- 只发送这一条消息，不要修改、删除或回复其它内容
- 发送完成后，直接回复：已发送
- 如果找不到该会话，直接回复：未找到会话
- 如果页面出现二维码、要求扫码登录，直接回复：需要扫码登录
- 如果出现滑块验证码/安全验证，直接回复：需要安全验证"""


# 给模型的任务：在所给视频的指定评论下回复固定内容
COMMENT_REPLY_TASK = """打开 {url} 视频号助手，进入「评论管理」页面。
找到视频「{video}」下、评论人「{author}」发表的那条评论，其内容为：「{content}」。
点击这条评论对应的「回复」按钮，在回复输入框中输入以下文字并发送：
---
{reply}
---
要求：
- 只回复这一条评论，不要回复其它评论，不要修改或删除任何内容
- 发送的内容必须与上面给的文字完全一致
- 发送完成后，直接回复：已回复
- 如果找不到该视频或该评论，直接回复：未找到评论
- 如果页面出现二维码、要求扫码登录，直接回复：需要扫码登录
- 如果出现滑块验证码/安全验证，直接回复：需要安全验证"""
