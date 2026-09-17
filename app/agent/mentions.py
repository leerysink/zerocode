"""@文件指定：解析消息中的 @路径 标记并展开为注入文本（spec §4.3）。

只注入一次：展开结果写入 message.context_content（旁路列），UI 与历史原文不受影响；
历史组装读 COALESCE(context_content, content)。文件缺失/越界/二进制/超限只注入占位说明，
不阻断发送。
"""
import re
from pathlib import Path
from app.agent.tools import BINARY_EXTS, resolve_in_workspace

MENTION_MAX_BYTES = 20 * 1024
MENTION_RE = re.compile(r'@"([^"\n]+)"|@([^\s@，。；、）】]+)')

def extract_mentions(text: str) -> list[str]:
    """提取唯一路径（保序）；@ 前是单词字符视为邮箱，跳过；尾部标点剔除。"""
    out, seen = [], set()
    for m in MENTION_RE.finditer(text or ""):
        start = m.start()
        if start > 0 and (re.match(r"[\w.]", text[start - 1] or "")):
            continue
        p = (m.group(1) or m.group(2)).strip().rstrip("，。；、）】:,.")
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out

def expand_mentions(root: Path, text: str, max_bytes: int = MENTION_MAX_BYTES) -> str | None:
    """把 @标记 展开为注入文本；无任何标记返回 None。"""
    mentions = extract_mentions(text)
    if not mentions:
        return None
    parts = []
    for p in mentions:
        try:
            f = resolve_in_workspace(root, p)
            if not f.is_file():
                parts.append(f"[文件不可用: {p}]")
            elif f.suffix.lower() in BINARY_EXTS:
                parts.append(f"[二进制文件 {p}，无法内联]")
            elif f.stat().st_size > max_bytes:
                parts.append(f"[大文件 {p}（{f.stat().st_size // 1024} KB），请用 read_file 分段读取]")
            else:
                body = f.read_text(encoding="utf-8", errors="replace")
                parts.append(f'<file path="{p}">\n{body}\n</file>')
        except Exception:
            parts.append(f"[文件不可用: {p}]")
    return "\n\n".join(parts)
