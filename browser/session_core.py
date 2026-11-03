"""浏览器 session 的纯函数层：ref 令牌、敏感判定、增量裁剪、expect 判定、回收判定。

刻意不 import playwright（同 `inspect_core.py` 的口径）：这些判定要在没有浏览器的
机器上跑单测，也要能被 sidecar 端点与将来的集成测试共用。
"""
import re
import secrets
from urllib.parse import urlsplit

REF_ATTR = "data-zc-ref"
# ref 是**每次扫描重新生成的随机短令牌**，不是按采集顺序排的序号。
# 序号那套在真机上跑出一个静默缺陷（规格 §15「ref 失效」）：每次采集都从头重排，于是上一轮
# 发给模型的旧 ref 在新清单里大概率仍然是一个合法序号，拿它去点会**点到另一个元素**而不是
# 响亮失败——顺序一变就删错行、点错按钮。令牌让旧 ref 在新页面上必然查不到，落回既有的
# 「ref 无效或页面已变化」那条出口（带回新清单 + 自动附图）。
REF_PREFIX = "r"
REF_TOKEN_LENGTH = 6
# 字母表去掉易混字符（`0 o 1 l i`）：句柄会出现在日志、步骤卡和模型的回述里，
# 把 `l` 看成 `1` 的代价是一次无法解释的错点。
REF_TOKEN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
REF_TOKEN_SPACE = len(REF_TOKEN_ALPHABET) ** REF_TOKEN_LENGTH
# 只认这一形状。字符集刻意不含任何需要转义进 CSS 属性选择器的字符（无引号、反斜杠、方括号、
# 空白、`#` `.` 等）——这正是令牌能被安全拼进 `[data-zc-ref="..."]` 的前提；
# `selector_for` 那道「非自生成形状一律拒绝」的闸因此只需要判形状。
REF_RE = re.compile(rf"^{REF_PREFIX}[{REF_TOKEN_ALPHABET}]{{{REF_TOKEN_LENGTH}}}$")
SENSITIVE_NAME_RE = re.compile(r"(token|secret|password|passwd|api[_-]?key|code)", re.IGNORECASE)
INTERACTIVE_MAX_DEFAULT = 40
# 浏览器侧一次遍历的节点上限（Task 1 携带项）：Python 侧的 40 条只裁「给模型看几条」，
# 打标与回传的代价从来不受它约束——大 SPA 会给上千个匹配元素写属性并整份序列化回来，
# 在 read_only + mem_limit 1g 的容器里这就是一次自伤。上限取 40 的 5 倍：留足「无名条目
# 被 Python 丢掉后仍能凑满 40 条」的余量，又不必在每次采集里为整页 DOM 买单。
INTERACTIVE_JS_SCAN_MAX = 200
# 一次扫描发出的令牌池 = 一次扫描最多能标的元素数。两个数**必须相等**（有测试钉住）：
# 「池用完」与「遍历触顶」是同一件事的两种说法，分开写的话池先耗尽会让 `shape_interactive`
# 的封顶判据（按节点数 >= SCAN_MAX）静默失效——模型看到少了条目的清单却写着 truncated:false。
REF_TOKEN_POOL = INTERACTIVE_JS_SCAN_MAX


def _draw_ref_token() -> str:
    return REF_PREFIX + "".join(secrets.choice(REF_TOKEN_ALPHABET)
                                for _ in range(REF_TOKEN_LENGTH))


def new_ref_tokens(count: int = REF_TOKEN_POOL, picker=None) -> list[str]:
    """为一次扫描生成 `count` 个互不相同的句柄令牌，顺序与打标脚本的元素顺序一一对应。

    刻意在 Python 侧生成、把数组当参数喂给脚本，不在 JS 里摇随机数：唯一性与形状策略要留在
    纯函数层可单测，而且这套策略只能有一处（两处各摇一次的后果是同一次扫描里两个元素拿到
    同一个句柄，点哪一个全看运气）。
    `picker` 是「抽一个令牌」这个动作的接缝（同 `sessions.await_expect` 的 `clock` / `sleeper`
    口径）：生产不传；测试用它喂一份**会重复**的随机源，好把下面那条 used 去重本身钉住——
    真随机数下这条永远测不出来（31^6 的空间里撞一次属于要专门构造的事件）。
    """
    draw = _draw_ref_token if picker is None else picker
    pool = max(int(count), 0)
    if pool > REF_TOKEN_SPACE:
        # 池比令牌空间还大时「互不相同」根本做不到。这不是会发生的量（31^6 ≈ 8.9 亿，
        # 扫描上限 200），但要发生也只能在这里响亮报错——不能让脚本拿着重复令牌静默打标。
        raise ValueError(f"令牌池 {pool} 超过句柄令牌空间 {REF_TOKEN_SPACE}")
    used: set[str] = set()
    tokens = []
    while len(tokens) < pool:
        token = draw()
        if token in used:
            continue        # 抽到重复就换一个，绝不把同一个句柄标给两个元素
        used.add(token)
        tokens.append(token)
    return tokens


def selector_for(ref) -> str | None:
    """把 ref 换成属性选择器。非自生成形状一律拒绝——ref 来自模型可控的参数面，
    拼进选择器前必须确认它只会是我们自己发出去的那种形状（形状与字符集见 `REF_RE` 上方：
    令牌字母表不含需要转义进 CSS 属性选择器的字符，判过形状就可以直接拼）。"""
    if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
        return None
    return f'[{REF_ATTR}="{ref}"]'


def is_sensitive(*, input_type: str, name: str, label: str) -> bool:
    """该输入框的值落库前要不要遮断。

    判定故意保守：`type=password` 直接算，其余看名称/标签命中关键词。非这两类的敏感输入
    （身份证号等）不在判定内，靠后端统一规则表兜——规格 §8 把它记为残余风险，不宣称解决。
    """
    if (input_type or "").lower() == "password":
        return True
    return bool(SENSITIVE_NAME_RE.search(f"{name or ''} {label or ''}"))


def shape_interactive(nodes: list[dict], limit: int = INTERACTIVE_MAX_DEFAULT):
    """把浏览器采回的原始节点裁成给模型看的清单。

    丢掉没有可辨识名称的条目：Agent 拿到一个 name 为空的 ref 只会瞎点。`ref` 由 Python 侧
    一次扫描生成一批随机令牌、脚本按顺序取（见 `new_ref_tokens` 与 `INTERACTIVE_JS`），
    这里不改写它——改名或重编号都会让模型手上的旧 ref 指向另一个元素，比失效更糟。
    """
    budget = max(int(limit), 1)
    items = []
    raw_nodes = list(nodes or [])
    named = 0  # 满了之后仍要数完：截断与否看的是原始清单里有多少条可辨识条目
    for raw in raw_nodes:
        name = str(raw.get("name") or "").strip()[:60]
        if not name:
            continue
        named += 1
        if len(items) >= budget:
            continue
        items.append({
            "ref": str(raw.get("ref") or ""),
            "role": str(raw.get("role") or raw.get("tag") or "element")[:20],
            "name": name,
            "disabled": bool(raw.get("disabled")),
            "sensitive": is_sensitive(input_type=str(raw.get("type") or ""),
                                      name=name, label=str(raw.get("label") or "")),
        })
    # 浏览器侧遍历封顶也算截断，而且要单独算：采到 SCAN_MAX 说明后面还有没看过的元素，
    # 哪怕这一屏里只有 3 个带名字（named > budget 为假）也不能对模型说「清单是完整的」。
    # 令牌池与 SCAN_MAX 同值（`REF_TOKEN_POOL`），所以「令牌用完」必然同时满足这个判据，
    # 不需要第二条截断信号。
    return items, (named > budget or len(raw_nodes) >= INTERACTIVE_JS_SCAN_MAX)


def new_only(seen: set[tuple], current: list[dict]):
    """本步新增的 console / 失败请求条目。

    每步回全量会让模型重读同一批旧错误，也会把「这个错是不是我这一步搞出来的」这个
    最有用的信号冲淡。key 用 (level, text) / (method, url, status) 这种粗粒度指纹，
    同一页反复出现的同一条错误算旧账——那正是我们想省掉的噪声。
    """
    fresh = []
    for item in current or []:
        # 两类条目形状不同，指纹也不同：把 console 条目也压成三元组的话，上一轮记下的
        # (level, text) 永远匹配不上，旧账全部会变成新账。
        if item.get("url") is not None:
            key = (str(item.get("method") or "").upper(), str(item.get("url") or ""),
                   str(item.get("status") or ""))
        else:
            key = (str(item.get("level") or ""), str(item.get("text") or ""))
        if key in seen:
            continue
        seen.add(key)
        fresh.append(item)
    return fresh, seen


# 三种形态的匹配对象见 `_url_forms`；下面三处 note 反复引用这句话，因为它是给模型的改写指引：
# 断言恒假不报错（规格 §6.3 轮询到超时），模型只看得到「不匹配」三个字，就会原样重试同一个模式。
_URL_MATCH_SYNTAX = ("url_matches 只支持结尾单个 `*` 通配，"
                     "模式可比的是完整 URL、`path?query` 或 `path`")


def _url_forms(url: str):
    """一条 URL 参与匹配的三种形态：完整 URL、`path?query`、`path`。

    只比 path 会丢掉 scheme/host 和 query，于是 `http://localhost:8210/orders` 与
    `/orders?id=4` 这两种模型最自然写出的模式恒假。这是本功能要修掉的那类静默失败，
    不能在新断言里复活，所以宁可多比两种形态。
    """
    raw = (url or "").strip()
    parts = urlsplit(raw)
    return (raw, parts.path + (f"?{parts.query}" if parts.query else ""), parts.path)


def _url_pattern_supported(pat: str) -> bool:
    """通配只支持结尾单个 `*`。中段通配（`/orders/*/edit`）不做实现是有意的取舍，
    但这个限制必须由 note 说出口——悄悄返回 False 跟只比 path 的恒假是同一个病。"""
    return "*" not in pat or (pat.count("*") == 1 and pat.endswith("*"))


def _url_matches(pattern: str, url: str) -> bool:
    pat = (pattern or "").strip()
    if not pat or not _url_pattern_supported(pat):
        return False
    if pat.endswith("*"):
        prefix = pat[:-1]
        return any(form.startswith(prefix) for form in _url_forms(url))
    return any(form in (pat, pat.rstrip("/") + "/") for form in _url_forms(url))


def _url_echo_shape(url: str) -> str:
    """给模型看的 URL 回显只取 `path?query`（裁定 R-17）。

    两件事一起决定不能整条回显：snapshot 里的 `url` 是 `_connect_url` 重写后的**内部容器
    IP**，进 note 就是进模型上下文与 run log（拓扑泄漏）；而模型下一轮本来就该写
    `path?query` 这个形状的模式，回显成对的形状比回显一条它复制过去也匹配不上的地址更有指导性。
    匹配仍然用完整 url（`_url_matches` 收的是 snapshot 原值），只是不外发。
    """
    forms = _url_forms(url)
    return (forms[1] or forms[2] or forms[0])[:80]


def _url_expect_note(pattern: str, url: str) -> str:
    """失败说明要能指导改写：模式写法本身不被支持时，光陈述「不匹配」等于让模型重试同一个模式。"""
    pat = (pattern or "").strip()
    cur = _url_echo_shape(url)
    if not pat:
        return f"url_matches 需要 value（{_URL_MATCH_SYNTAX}）；当前 URL：{cur}"
    if not _url_pattern_supported(pat):
        return (f"模式「{pat[:40]}」写法不被支持：{_URL_MATCH_SYNTAX}。"
                f"请去掉中段的 * 或把它移到结尾，例：/orders*；当前 URL：{cur}")
    return f"当前 URL「{cur}」不匹配模式「{pat[:40]}」（{_URL_MATCH_SYNTAX}）"


def judge_expect(expect: dict, snapshot: dict, console_new: list[dict]):
    """按 spec §6.3 判一条断言。返回 `(passed, note)`；note 解释为什么不通过。

    四种断言只读快照，不再回浏览器取第二次——快照本来就是动作后采的一次状态。
    """
    kind = str((expect or {}).get("type") or "")
    value = str((expect or {}).get("value") or "").strip()
    if kind == "text_contains":
        if not value:
            return False, "text_contains 需要 value"
        return value in (snapshot.get("text") or ""), f"页面文本未包含「{value[:40]}」"
    if kind == "element_visible":
        if not value:
            return False, "element_visible 需要 value"
        hit = any(value == i.get("name") or value in str(i.get("name") or "")
                  for i in snapshot.get("interactive") or [])
        return hit, f"交互清单里没有「{value[:40]}」"
    if kind == "url_matches":
        url = str(snapshot.get("url") or "")
        return _url_matches(value, url), _url_expect_note(value, url)
    if kind == "no_new_console_error":
        return not console_new, f"本步新增 {len(console_new)} 条控制台错误"
    return False, f"未知断言类型: {kind or '（空）'}"


def can_open(active: int, limit: int) -> bool:
    """能否再开一个 session。超限是**拒绝**而不是排队：排队等于让一次工具调用挂几十秒，
    模型只会把它读成超时，丢掉「服务已满」这个可行动的原因。"""
    return limit <= 0 or int(active) < limit


def expired(last_used: float, now: float, ttl_seconds: int) -> bool:
    return ttl_seconds > 0 and (now - last_used) > ttl_seconds


# 一次遍历同时完成采集与打标：分两趟会让第二趟选到的元素与第一趟不一致
# （Vue 的异步渲染就发生在那两次往返之间）。旧属性先清掉——上一轮的 ref 必须作废，
# 而「作废」要能被机制真的检测到，靠的是新令牌与旧令牌几乎不可能相同（`new_ref_tokens`），
# 不是靠序号从头重排（旧序号在新清单里照样合法，于是会静默点到另一个元素）。
# 令牌由 Python 侧生成、整个数组当参数传进来：脚本自己**不摇随机数**，
# 唯一性与形状策略只在 `session_core` 这一处定义。
# 脚本里的 attr 字面量必须与 REF_ATTR 同值：改了 REF_ATTR 忘了这里，每个点击都会被
# selector_for 判非法，症状出现在另一个文件里，很难定位。
# 遍历上限用占位符从 Python 常量注入，不在 JS 里再写一遍字面量：两处数字漂开的话，
# `shape_interactive` 的「采到封顶」判据会静默失效，模型看到的清单少了条目却写着 truncated:false。
_INTERACTIVE_JS_TEMPLATE = """
(tokens) => {
  const SEL = ['a[href]', 'button', 'input', 'select', 'textarea',
               '[role=button]', '[role=link]', '[role=menuitem]', '[role=tab]',
               '[role=checkbox]', '[role=radio]', '[role=combobox]', '[role=switch]',
               '[onclick]', '[tabindex]:not([tabindex="-1"])'].join(',');
  const attr = 'data-zc-ref';
  const SCAN_MAX = __ZC_SCAN_MAX__;
  const pool = Array.isArray(tokens) ? tokens.length : 0;
  for (const el of document.querySelectorAll('[' + attr + ']')) el.removeAttribute(attr);
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    // 两个封顶并列判：SCAN_MAX 是资源闸，pool 是令牌池。后者只是防越界（越界会拿到
    // undefined 并把「undefined」当句柄打出去），因为池与 SCAN_MAX 同值，触顶时
    // `shape_interactive` 的封顶判据仍然成立、清单照旧报 truncated。
    if (out.length >= SCAN_MAX || out.length >= pool) break;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const label = el.getAttribute('aria-label')
      || (el.labels && el.labels[0] ? el.labels[0].textContent : '')
      || el.getAttribute('placeholder') || el.getAttribute('title') || '';
    const text = (el.textContent || '').trim().slice(0, 60);
    // 一个局部变量算一次、回传与 setAttribute 两处共用：两处各写一遍就会标一个、报一个。
    const ref = tokens[out.length];
    out.push({
      ref,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      label: (label || '').trim().slice(0, 60),
      name: (label || text || '').trim().slice(0, 60),
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
    });
    el.setAttribute(attr, ref);
  }
  return out;
}
"""

INTERACTIVE_JS = _INTERACTIVE_JS_TEMPLATE.replace("__ZC_SCAN_MAX__", str(INTERACTIVE_JS_SCAN_MAX))
