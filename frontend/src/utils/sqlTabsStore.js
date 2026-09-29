// 查询页签的本地持久化（Ticket 005 / ADR 0008）。
//
// 为什么走 localStorage 而不是服务端脚本库：页签是"我这个人这台浏览器的工作现场"，
// 不是团队资产；建表、鉴权、同步的代价远大于收益（详见 ADR 0008）。
//
// 三条硬约束决定了本文件的写法：
// 1. **只存脚本不存结果**：落盘前把 tab 削成 {id, name, sql}，state 也只留
//    {version, active, tabs}——结果块可能几千行，塞进 5MB 配额既挤爆又没意义（US-05）。
// 2. **读永远不抛**：浏览器里躺着的东西不可信（用户手改、老版本残留、配额被裁），
//    任何异常形状一律退回默认单页签，让用户从空白开始，而不是白屏。
// 3. **写可能失败**：配额满只降级为"本次会话不再自动保存"（调用方拿 ok:false 显示黄条），
//    编辑器文本绝不为存储问题让路。
//
// storage 做成注入点：测试用假对象，组件里落回 globalThis.localStorage，
// 隐私模式下连 localStorage 本身都取不到，同样走降级路径而不是崩。
export const TABS_VERSION = 1
// 页签上限：再多就不是"工作区"而是"文件管理器"了，且 12 × 大 SQL 已接近配额舒适区。
export const MAX_TABS = 12

const DEFAULT_TAB_ID = 'query_1'

/** 页签存储键：按 (appId, env) 分区，dev 与 prod 各一套，切环境即切工作区（US-06）。 */
export function tabsKey(appId, env) {
  return `zc.dbq.tabs.${appId}.${env}`
}

/** 默认状态：一个空白页签。页签数永远 ≥1，界面不允许"无页签"死状态。 */
export function createTabsState() {
  return { version: TABS_VERSION, active: 0, tabs: [blankTab(DEFAULT_TAB_ID)] }
}

/** 读页签。无 key / JSON 坏 / version 不符 / 形状不对 → 默认单页签，任何情况下都不抛。 */
export function loadTabs(appId, env, storage) {
  const st = resolveStorage(storage)
  if (!st) return createTabsState()
  let raw = null
  try {
    raw = st.getItem(tabsKey(appId, env))
  } catch {
    return createTabsState()
  }
  return toState(parseJson(raw))
}

/** 写页签。返回 {ok:true} 或 {ok:false, reason:'quota'}——quota 涵盖"配额满"与"存储不可用"，
 *  两者的处置动作完全一致（停止自动保存 + 黄条），调用方不需要第二条分支。 */
export function saveTabs(appId, env, state, storage) {
  const st = resolveStorage(storage)
  if (!st) return { ok: false, reason: 'quota' }
  // 先归一化再序列化：运行时字段（results/busy…）在这一步被削掉，白名单是唯一出口。
  const payload = JSON.stringify(toState(state))
  try {
    st.setItem(tabsKey(appId, env), payload)
  } catch {
    return { ok: false, reason: 'quota' }
  }
  return { ok: true }
}

/**
 * 新建页签：命名取**最小空闲**的 query_N（关掉 query_2 后再建就复用它，不留空洞、不无限递增）。
 * @returns 达到上限时返回 null（调用方提示"已达上限"），否则返回新 state；传入 state 不被改动。
 */
export function addTab(state) {
  const tabs = Array.isArray(state?.tabs) ? state.tabs : []
  if (tabs.length >= MAX_TABS) return null
  const id = nextFreeId(tabs)
  return { version: TABS_VERSION, active: tabs.length, tabs: tabs.map(cloneTab).concat([blankTab(id)]) }
}

/**
 * 关闭页签。关掉的就是当前页签时，激活位自然落到后一页；关掉最后一个页签后自动补一个
 * 空白页签（不允许 0 页签）。id 不存在时原样返回——连点 × 不该把页签掏空。
 */
export function removeTab(state, id) {
  const tabs = Array.isArray(state?.tabs) ? state.tabs : []
  const at = tabs.findIndex((t) => t && t.id === id)
  if (at === -1) return state
  const rest = tabs.filter((_, i) => i !== at).map(cloneTab)
  if (!rest.length) return createTabsState()
  // 关的是当前页**之前**的页：激活项左移一位跟着走；关的就是当前页：后一页接手
  // （它移除后正好坐在 at 位），关的偏偏是最后一页时退到前一位——不能跳回第一页。
  const wanted = at < state.active ? state.active - 1 : Math.min(at, rest.length - 1)
  const nextActive = clampActive(wanted, rest.length)
  return { version: TABS_VERSION, active: nextActive, tabs: rest }
}

/**
 * 重命名：只改标题，id 不动（id 是结果区与持久化追踪的锚）。
 * 空白名原样返回传入的 state——不允许页签标题被改成空串。
 */
export function renameTab(state, id, name) {
  const clean = typeof name === 'string' ? name.trim() : ''
  if (!clean) return state
  const tabs = (Array.isArray(state?.tabs) ? state.tabs : []).map((t) => (
    t && t.id === id ? { ...t, name: clean } : cloneTab(t)
  ))
  return { version: TABS_VERSION, active: clampActive(state?.active, tabs.length), tabs }
}

function blankTab(id) {
  return { id, name: id, sql: '' }
}

/**
 * 结构操作用的浅拷贝：**保留运行时字段**（组件把 results/sentCount 挂在页签对象上，
 * 新建/关闭/重命名都不该把别的页签的结果区削没）。持久化的唯一出口是 cleanTab，
 * 运行时字段永远进不了 localStorage。
 */
function cloneTab(t) {
  return { ...t }
}

function nextFreeId(tabs) {
  const used = new Set(tabs.map((t) => t && t.id))
  for (let n = 1; ; n++) {
    const id = `query_${n}`
    if (!used.has(id)) return id
  }
}

function clampActive(value, length) {
  if (!Number.isInteger(value) || value < 0) return 0
  return value >= length ? 0 : value
}

function parseJson(raw) {
  if (typeof raw !== 'string' || !raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

/**
 * 把"任何东西"归一成合法 state：读盘与落盘共用同一个口径，避免"写进去的读不出来"。
 * 非法条目逐个丢弃；全丢光了（或 version 不符、tabs 不是数组）退回默认单页签——
 * 宁可丢损坏的那半截，也不让页签栏渲染出 undefined 标题。
 */
function toState(obj) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return createTabsState()
  if (obj.version !== TABS_VERSION) return createTabsState()
  const tabs = (Array.isArray(obj.tabs) ? obj.tabs : []).map(cleanTab).filter(Boolean)
  if (!tabs.length) return createTabsState()
  return { version: TABS_VERSION, active: clampActive(obj.active, tabs.length), tabs }
}

function cleanTab(t) {
  if (!t || typeof t !== 'object') return null
  if (typeof t.id !== 'string' || !t.id) return null
  const name = typeof t.name === 'string' && t.name.trim() ? t.name : t.id
  const sql = typeof t.sql === 'string' ? t.sql : ''
  return { id: t.id, name, sql }
}

function resolveStorage(injected) {
  if (injected !== undefined) return injected
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null // 某些隐私模式下取 localStorage 本身就抛
  }
}
