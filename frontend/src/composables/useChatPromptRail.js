// 提问导航条的量测层：把 DOM 几何喂给 promptRailModel 的纯判定。
//
// 这里刻意只留 I/O：读 offsetTop/scrollHeight、挂尺寸监听、按提问下标建量测表。
// 位置映射与高亮规则都在 promptRailModel.js，因为 happy-dom 没有布局引擎，这些读数在测试里
// 恒为 0——留在组件侧就等于把最关键的那条断言写成假绿。
import { computed, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import { MIN_RAIL_PROMPTS, buildPromptMarks, collectPrompts } from '../stores/promptRailModel'

// 提问气泡上的锚点属性，由 ChatMessages 挂载；值是该条在归一化 view 里的下标
export const PROMPT_INDEX_ATTR = 'data-prompt-index'

export function useChatPromptRail({ scrollRef, view }) {
  const prompts = computed(() => collectPrompts(view.value))
  // 量测表：{ [view 下标]: { top, height } }，top 相对滚动容器内容顶边
  const measured = ref({})
  const geometry = ref({ scrollHeight: 0, clientHeight: 0, scrollTop: 0 })

  function measure() {
    const el = scrollRef.value
    if (!el) return
    const table = {}
    for (const node of el.querySelectorAll(`[${PROMPT_INDEX_ATTR}]`)) {
      const key = Number(node.getAttribute(PROMPT_INDEX_ATTR))
      if (Number.isInteger(key)) table[key] = { top: node.offsetTop, height: node.offsetHeight }
    }
    measured.value = table
    geometry.value = {
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      scrollTop: el.scrollTop,
    }
  }

  // 滚动只更新 scrollTop：不重读 offsetTop，避免每帧强制回流
  function onScroll(event) {
    geometry.value = { ...geometry.value, scrollTop: event.target.scrollTop }
  }

  const marks = computed(() => {
    if (prompts.value.length < MIN_RAIL_PROMPTS) return []
    return buildPromptMarks({
      prompts: prompts.value,
      measured: measured.value,
      ...geometry.value,
    }).marks
  })

  // 条数变了才重量位置：流式增量只长最后一条，它下面没有提问，重算是白付的回流代价
  watch(() => [view.value.length, prompts.value.length], () => nextTick(measure))

  // 容器变高变矮（侧栏拖宽、窗口缩放）会让量测位置过期，跟着重量一次
  // 位置只有挂载后才量得到，所以横杠比正文晚一帧出现：一帧内轨道为空是可接受的，不占位
  let ro = null
  onMounted(() => {
    measure()
    if (typeof ResizeObserver === 'undefined' || !scrollRef.value) return
    ro = new ResizeObserver(() => measure())
    ro.observe(scrollRef.value)
  })
  onScopeDispose(() => ro?.disconnect())

  function scrollToPrompt(index) {
    const el = scrollRef.value
    const box = measured.value[index]
    if (!el || !box) return
    // 留 12px 上边距，免得气泡贴着容器边被裁掉半行
    el.scrollTo({ top: Math.max(0, box.top - 12), behavior: 'smooth' })
  }

  return { marks, measure, onScroll, scrollToPrompt }
}
