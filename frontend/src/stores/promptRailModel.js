// 提问导航条（prompt rail）的判定层：文案、可导航提问的挑选、minimap 位置映射、视口内高亮。
//
// 这里只有数字进数字出，DOM 量测留在 composables/useChatPromptRail.js。分开的理由是测试：
// happy-dom 没有真实布局，offsetTop/scrollHeight 恒为 0，把映射写在组件里就只能对着全 0
// 断言出一个假绿；把算式抽出来喂夹具数字，才量得到「横杠跟不跟着提问走」这件事。
//
// index 用提问在归一化 view 里的下标，与 ChatMessages 的 v-for 同源：归一化刻意丢掉 id/seq
// （见 messageModel.js），下标是渲染层唯一稳定的身份。量测结果也按这个下标配对，
// 不按数组顺序——被丢弃的空提问会在中间留洞，按顺序配对会让整条轨道错位。

// 少于两条提问没有导航价值——只有一条时横杠既定位不了也比不出位置，纯属占右缘。
export const MIN_RAIL_PROMPTS = 2

// 浮层展示文案：正文优先，只有附件时退成文件名清单，两者都没有则返回空串（调用方不画横杠）。
export function promptLabelOf(msg) {
  if (!msg || msg.role !== 'user') return ''
  const text = (msg.content || '').trim()
  if (text) return text
  const names = (msg.attachments || []).map((a) => (a && a.filename ? a.filename : '')).filter(Boolean)
  return names.length ? `附件：${names.join('、')}` : ''
}

// 从归一化消息里挑出可导航的提问，index 保留它在 view 中的下标。
export function collectPrompts(view) {
  const out = []
  for (let i = 0; i < (view || []).length; i++) {
    const label = promptLabelOf(view[i])
    if (label) out.push({ index: i, label })
  }
  return out
}

function clampPct(value) {
  if (!(value > 0)) return 0
  return value < 100 ? value : 100
}

/**
 * @param prompts      collectPrompts 的结果
 * @param measured     量测表，键是提问的 view 下标：{ [index]: { top, height } }，
 *                     top 相对滚动内容顶边。缺键＝这条还没量到，不画横杠。
 * @param scrollHeight 滚动内容总高
 * @param clientHeight 可视区高
 * @param scrollTop    当前滚动位置
 * @returns {{ marks: Array, activeIndex: number|null }} activeIndex 同样是 view 下标
 */
export function buildPromptMarks({ prompts, measured, scrollHeight, clientHeight, scrollTop }) {
  const empty = { marks: [], activeIndex: null }
  if (!prompts || prompts.length < MIN_RAIL_PROMPTS) return empty
  if (!(scrollHeight > 0) || !measured) return empty

  const items = prompts
    .map((p) => {
      const box = measured[p.index]
      return box ? { index: p.index, label: p.label, top: box.top, height: box.height } : null
    })
    .filter(Boolean)
  if (!items.length) return empty

  // 高亮 = 提问盒与视口有交叠；视口内有多条时取文档序最靠上的那条
  const inView = items.find((it) => it.top < scrollTop + clientHeight && it.top + it.height > scrollTop)
  // 视口落在两条提问之间的空档时，退而高亮最后一条已整体滚过的提问
  let passed = null
  for (const it of items) {
    if (it.top + it.height <= scrollTop) passed = it.index
  }
  const activeIndex = inView ? inView.index : passed

  const marks = items.map((it) => ({
    index: it.index,
    label: it.label,
    // minimap 映射：横杠在轨道里的比例 = 提问在滚动内容里的真实比例
    topPct: clampPct((it.top / scrollHeight) * 100),
    active: it.index === activeIndex,
  }))
  return { marks, activeIndex }
}
