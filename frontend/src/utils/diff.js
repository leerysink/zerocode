// 解析 unified diff 文本为逐行分类数组，供前端 diff 视图渲染
export function parseDiff(text) {
  return String(text ?? '')
    .split('\n')
    .map(line => {
      if (line.startsWith('@@')) return { type: 'hunk', text: line }
      if (line.startsWith('+')) return { type: 'add', text: line }
      if (line.startsWith('-')) return { type: 'del', text: line }
      return { type: 'ctx', text: line }
    })
}
