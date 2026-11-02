"""浏览器 session 的纯函数层：ref、敏感判定、增量裁剪、expect 判定、回收判定。

刻意不 import playwright（同 `inspect_core.py` 的口径）：这些判定要在没有浏览器的
机器上跑单测，也要能被 sidecar 端点与将来的集成测试共用。
"""
import re
from urllib.parse import urlsplit

REF_ATTR = "data-zc-ref"
REF_RE = re.compile(r"^e[1-9]\d{0,5}$")
SENSITIVE_NAME_RE = re.compile(r"(token|secret|password|passwd|api[_-]?key|code)", re.IGNORECASE)
INTERACTIVE_MAX_DEFAULT = 40


def selector_for(ref) -> str | None:
    """把 ref 换成属性选择器。非自生成形状一律拒绝——ref 来自模型可控的参数面，
    拼进选择器前必须确认它只会是我们自己发出去的那种形状。"""
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

    丢掉没有可辨识名称的条目：Agent 拿到一个 name 为空的 ref 只会瞎点。`ref` 由浏览器侧
    脚本按采集顺序生成（见 `INTERACTIVE_JS`），这里不重新编号——重编号会让模型手上的旧 ref
    指向另一个元素，比失效更糟。
    """
    budget = max(int(limit), 1)
    items = []
    named = 0  # 满了之后仍要数完：截断与否看的是原始清单里有多少条可辨识条目
    for raw in nodes or []:
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
    return items, named > budget


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


def _url_matches(pattern: str, url: str) -> bool:
    path = urlsplit(url or "").path
    pat = (pattern or "").strip()
    if not pat:
        return False
    if pat.endswith("*"):
        return path.startswith(pat[:-1])
    return path == pat or path == pat.rstrip("/") + "/"


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
        return _url_matches(value, snapshot.get("url") or ""), f"当前 URL 不匹配 {value}"
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
# （Vue 的异步渲染就发生在那两次往返之间）。旧属性先清掉——上一轮的 ref 必须作废。
# 脚本里的 attr 字面量必须与 REF_ATTR 同值：改了 REF_ATTR 忘了这里，每个点击都会被
# selector_for 判非法，症状出现在另一个文件里，很难定位。
INTERACTIVE_JS = """
() => {
  const SEL = ['a[href]', 'button', 'input', 'select', 'textarea',
               '[role=button]', '[role=link]', '[role=menuitem]', '[role=tab]',
               '[role=checkbox]', '[role=radio]', '[role=combobox]', '[role=switch]',
               '[onclick]', '[tabindex]:not([tabindex="-1"])'].join(',');
  const attr = 'data-zc-ref';
  for (const el of document.querySelectorAll('[' + attr + ']')) el.removeAttribute(attr);
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const label = el.getAttribute('aria-label')
      || (el.labels && el.labels[0] ? el.labels[0].textContent : '')
      || el.getAttribute('placeholder') || el.getAttribute('title') || '';
    const text = (el.textContent || '').trim().slice(0, 60);
    const ref = 'e' + (out.length + 1);
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
