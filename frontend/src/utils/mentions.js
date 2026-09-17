// @ 文件引用解析：提取光标前正在输入的 @词；选择后替换为带引号路径（spec §6）
export function activeMentionToken(text, caret) {
  const before = (text || '').slice(0, caret)
  const m = before.match(/@"?([^"\n]*)$/)
  if (!m) return null
  const start = caret - m[0].length
  if (start > 0 && /[\w.]/.test(before[start - 1])) return null // 邮箱等场景不触发
  return { query: m[1], start, end: caret }
}

export function applyMention(text, token, path) {
  return text.slice(0, token.start) + '@"' + path + '" ' + text.slice(token.end)
}
