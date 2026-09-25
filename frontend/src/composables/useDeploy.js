// frontend/src/composables/useDeploy.js
// 部署事件流：/ws/deploy/{appId} 的订阅封装（M2 / T9，规格 04 §5 事件表）。
// 状态即数据——steps/buildTail/lastDone/prodStatus 全部是 ref，组件直接渲染；
// on() 只作副作用通道（弹通知、驱动确认框等），两处消费同一事件源不互斥。
// 断线重连：指数退避 1s→2s→4s…上限 15s；open 成功即复位。close() 后不再重连
// （组件卸载必须调，防后台幽灵连接——T10 单测断言这一点）。
import { ref } from 'vue'
import { wsUrl } from '../api'

const STEPS = ['preflight', 'build', 'provision', 'migrate', 'deploy', 'health', 'record']
const TAIL_MAX = 300

export function useDeploy(appId) {
  const connected = ref(false)
  const steps = ref(Object.fromEntries(STEPS.map(s => [s, 'wait'])))
  const buildTail = ref([])
  const prodStatus = ref(null)
  const devStatus = ref(null)
  const lastDone = ref(null)
  const migrateResult = ref(null)
  const handlers = new Map()
  let ws = null, timer = null, backoff = 1000, closedByUs = false

  function pushTail(line, kind) {
    // 只留尾部 TAIL_MAX 行：发布日志是可再生的观测流，全量驻留内存没有意义
    buildTail.value = [...buildTail.value.slice(-(TAIL_MAX - 1)), { line, kind, ts: Date.now() }]
  }
  function reset() {
    // 组件在再次受理发布/回滚前调用：事件流本身不带「新一轮开始」哨兵，
    // publish_step 是增量累积的，上一轮的 ok 会伪装成这一轮的进度。
    steps.value = Object.fromEntries(STEPS.map(s => [s, 'wait']))
    buildTail.value = []
    lastDone.value = null
  }

  function on(type, fn) {
    const key = type.startsWith('deploy_') ? type : 'deploy_' + type
    if (!handlers.has(key)) handlers.set(key, [])
    handlers.get(key).push(fn)
  }
  function fire(ev) {
    const key = 'deploy_' + ev.type
    ;(handlers.get(key) || []).forEach(fn => {
      try { fn(ev) } catch (e) { console.error('useDeploy handler', e) }
    })
  }

  function connect() {
    if (ws || !appId) return
    closedByUs = false
    ws = new WebSocket(wsUrl(`/ws/deploy/${appId}`))
    ws.addEventListener('open', () => { connected.value = true; backoff = 1000 })
    ws.addEventListener('message', (m) => {
      let ev
      try { ev = JSON.parse(m.data) } catch { return }
      if (ev.type === 'ping') return
      switch (ev.type) {
        case 'publish_step':
          steps.value = { ...steps.value, [ev.step]: ev.state }
          break
        case 'build_log': pushTail(ev.line, 'build'); break
        case 'dev_log': pushTail(ev.line, 'step'); break
        case 'migrate_result':
          migrateResult.value = ev
          pushTail(`迁移 ${ev.ok ? '成功' : '失败'}（${ev.env}）`, 'step')
          break
        case 'dev_status':
          devStatus.value = ev.status
          if (ev.message) pushTail(`✗ ${ev.message}`, 'step')
          break
        case 'prod_status':
          prodStatus.value = ev.status
          if (ev.message) pushTail(`✗ ${ev.message}`, 'step')
          break
        case 'publish_done': lastDone.value = ev; break
      }
      fire(ev)
    })
    ws.addEventListener('close', (ev) => {
      ws = null
      connected.value = false
      // WS 认证收口（Ticket 009）：4401=未登录拒→登录页；4404=资源不可用→安静停下。
      // 两者都不是网络抖动，落进退避重连只会无限重复被拒的握手。
      if (ev?.code === 4401) { window.location.assign('/login'); return }
      if (ev?.code === 4404) return
      if (!closedByUs) {
        timer = setTimeout(connect, backoff)
        backoff = Math.min(backoff * 2, 15000)
      }
    })
    ws.addEventListener('error', () => { /* close 会跟在 error 后触发，统一在 close 处理 */ })
  }
  function close() {
    closedByUs = true
    if (timer) clearTimeout(timer)
    ws?.close()
    ws = null
    connected.value = false
  }
  return { connected, steps, buildTail, prodStatus, devStatus, lastDone, migrateResult,
    connect, close, reset, on }
}
