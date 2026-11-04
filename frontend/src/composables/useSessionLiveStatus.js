// 会话状态角标的实时读数（spec 2026-11-04 US-9/14）。
// 按账号轮询 /sessions/live-status，5s 一次；聊天页常驻后台标签，页面隐藏时必须停摆。
// 刻意不订阅 WS：聊天页已有一条 /ws/chat/{sessionId}，状态提示不值得再开第二条连接。
import { onBeforeUnmount, ref } from 'vue'
import { getSessionLiveStatuses } from '../api'

export const POLL_MS = 5000

export function useSessionLiveStatus() {
  // session_id → { running, waiting, last_outcome, finished_at }
  const statuses = ref({})
  let timer = null
  let stopped = false
  let inFlight = false

  async function tick() {
    try {
      const { data } = await getSessionLiveStatuses()
      const map = {}
      for (const row of data.sessions || []) map[row.session_id] = row
      statuses.value = map
    } catch {
      // 轮询失败静默：状态未知时角标沿用上一次读数，不把网络错误弹到聊天页上
    }
  }

  // 自我续期的 setTimeout 链而不是 setInterval：页面隐藏时要能就地断链，
  // setInterval 只能靠 clearInterval 另起一套状态。
  async function loop() {
    // 单链守卫：隐藏后立刻切回时，上一次 tick 可能还在路上、timer 仍为空，
    // onVisibility 会再叫一次 loop；不拦就会叠出两条续期链（双倍轮询）。
    if (stopped || inFlight) return
    inFlight = true
    timer = null
    try {
      await tick()
    } finally {
      inFlight = false
    }
    if (stopped || document.hidden) {
      timer = null // 断链；重新可见时由 onVisibility 续上
      return
    }
    timer = setTimeout(loop, POLL_MS)
  }

  function onVisibility() {
    if (document.hidden) {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
    } else if (!timer) {
      loop() // 切回来立即补一次，不等下个周期
    }
  }
  document.addEventListener('visibilitychange', onVisibility)

  loop()

  onBeforeUnmount(() => {
    stopped = true
    if (timer) clearTimeout(timer)
    timer = null
    document.removeEventListener('visibilitychange', onVisibility)
  })

  return { statuses }
}
