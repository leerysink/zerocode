// 预览状态轮询（票 002 / spec §5.3）。
// 读 GET /api/apps/{id} 而不是 GET /apps/{id}/dev/status：后者每次都 `docker compose ps`
// 起一个子进程（engine.py::dev_status），拿它当 5s 轮询目标是拿容器运行时接口做数据库读。
// 刻意不订阅 /ws/deploy/{id}——聊天页已有一条 /ws/chat/{sessionId}，理由见 ADR-0020 后果条。
import { onBeforeUnmount, ref, watch } from 'vue'
import { getApp } from '../api'

export const POLL_MS = 5000
export const POLL_STARTING_MS = 2000
export const STARTING_TIMEOUT_MS = 90000

export function useDevStatus(appIdRef, options = {}) {
  const status = ref(null)
  const url = ref('')
  let timer = null
  let startingSince = null
  let reported = null

  // 只在进入终态时播报一次；一直 running 不该每 5s 报一回
  function report(kind) {
    if (reported === kind) return
    reported = kind
    options.onSettled?.(kind)
  }

  // 用户重新发起启停时解除去重：否则「本来就 running → 点重启 → 回到 running」这一圈
  // 会被当成重复播报吞掉，按钮永远转圈解不开。
  function rearm() {
    reported = null
    startingSince = null
  }

  async function tick() {
    const id = appIdRef.value
    if (id == null) return
    try {
      const { data } = await getApp(id)
      status.value = data.status_dev
      url.value = data.preview_url || ''
    } catch {
      // 轮询失败静默：状态未知时按钮沿用上一次读数，不许把错误弹到用户脸上打断聊天
      return
    }
    if (status.value === 'starting') {
      if (startingSince == null) startingSince = Date.now()
      else if (Date.now() - startingSince >= STARTING_TIMEOUT_MS) report('timeout')
    } else {
      startingSince = null
      if (status.value === 'running') report('running')
      else if (status.value === 'error') report('error')
    }
  }

  // 自我续期的 setTimeout 链而不是 setInterval：starting 要降到 2s，固定周期做不到换档；
  // 页面隐藏时要能就地断链，setInterval 只能靠 clearInterval 另起一套状态。
  async function loop() {
    await tick()
    if (document.hidden) {
      timer = null // 断链；重新可见时由 onVisibility 续上
      return
    }
    const ms = status.value === 'starting' ? POLL_STARTING_MS : POLL_MS
    timer = setTimeout(loop, ms)
  }

  // 聊天页常驻后台标签，不停摆就是拿没人看的页面每分钟白打 12 次数据库
  function onVisibility() {
    if (document.hidden) {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
    } else if (!timer) {
      loop() // 切回来立即补一次，不等下个周期——用户回来就是想马上看到状态
    }
  }
  document.addEventListener('visibilitychange', onVisibility)

  watch(
    appIdRef,
    () => {
      // 切应用即清读数，免得把上一个应用的状态显示在新应用上；immediate 是因为
      // 挂载时 listApps 还在飞，id 到位这一刻才真正开始轮询。
      if (timer) clearTimeout(timer)
      status.value = null
      url.value = ''
      rearm()
      loop()
    },
    { immediate: true }
  )

  onBeforeUnmount(() => {
    if (timer) clearTimeout(timer)
    timer = null
    document.removeEventListener('visibilitychange', onVisibility)
  })

  return { status, url, rearm }
}
