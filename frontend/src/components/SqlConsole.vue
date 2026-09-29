<script setup>
// 查询控制台：多页签，每页签一套脚本与结果块，**遇错即停**且失败可见（Ticket 003/004/005）。
//
// 后端在同一连接内按序执行，某条报错则该条 status='error'、其后所有可执行条
// status='skipped' 并立即停止，已成功的条目**不回滚**（ADR 0007）。
//
// 页签（Ticket 005 / ADR 0008）：
// - 一个页签持有自己的 SQL 文本与结果块列表；结果只活在内存，刷新后为空（US-05）。
// - 页签按 (appId, env) 分区落 localStorage，切环境 = 切整套页签（替掉原「切 env 清 sqlText」）。
// - 写入失败（配额满 / 存储不可用）→ 黄条提示且本次会话不再尝试写入，编辑器文本绝不让路。
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { CloseOutlined, PlayCircleOutlined, PlusOutlined } from '@ant-design/icons-vue'
import { dbSqlBatch } from '../api'
import SqlEditor from './SqlEditor.vue'
import { splitSqlStatements } from '../utils/sqlSplit'
import { isReadOnlySelect } from '../utils/sqlClassify'
import { MAX_TABS, addTab, loadTabs, removeTab, renameTab, saveTabs } from '../utils/sqlTabsStore'

const props = defineProps({
  appId: { type: [String, Number], required: true },
  env: { type: String, required: true },
  tables: { type: Array, default: () => [] },
})

// 页签对象在内存里多带几个运行时字段（results / sentCount / runToken），落盘时由 store 的
// 白名单削掉——运行态与持久态共用一个对象，省掉一份“id → 结果”的旁路映射。
function withRuntime(s) {
  for (const t of s.tabs) {
    if (!Array.isArray(t.results)) t.results = []
    if (!Number.isInteger(t.sentCount)) t.sentCount = 0
  }
  return s
}

const state = ref(withRuntime(loadTabs(props.appId, props.env)))
// 内存里这套页签属于哪一对 (appId, env)：换环境时要先把它还给**旧键**再读新键，
// 否则会把 dev 的打字进度写进 prod。
let bound = { appId: props.appId, env: props.env }
const saveBlocked = ref(false)

const current = computed(() => state.value.tabs[state.value.active] ?? state.value.tabs[0])
const text = computed({
  get: () => current.value.sql,
  set: (v) => { current.value.sql = v; scheduleSave() },
})
const results = computed(() => current.value.results)
// 块头分母用**发出去的条数**而不是 results.length：服务端会丢弃纯注释段，
// 结果集的 index 是原数组下标（可能稀疏），用 length 会显示成「第 3/1 条」。
const sentCount = computed(() => current.value.sentCount)
const busy = ref(false)

// 汇总条：共 N 条，成功 X[，失败于第 i 条][，剩余 M 条未执行]。
// 失败子句只在真有 error 时出现，剩余子句只在真有 skipped 时出现——
// 全成功时不能挂着「失败于」糊人。
const summary = computed(() => {
  if (!results.value.length) return ''
  const ok = results.value.filter((r) => r.status === 'ok').length
  const failed = results.value.find((r) => r.status === 'error')
  const skipped = results.value.filter((r) => r.status === 'skipped').length
  let out = `共 ${sentCount.value} 条，成功 ${ok}`
  if (failed) out += `，失败于第 ${(failed.index ?? 0) + 1} 条`
  if (skipped) out += `，剩余 ${skipped} 条未执行`
  return out
})

// ---- 本地保存：打字走防抖（每键都同步写 localStorage 会在长脚本上卡输入），
// 结构性动作（新建/关闭/重命名/切页/卸载）立即落盘。
const SAVE_DEBOUNCE_MS = 400
let saveTimer = null

function persist() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  if (saveBlocked.value) return
  // 配额满与存储整个不可用在 store 里归为同一个 reason：黄条只弹一次，之后不再尝试
  if (!saveTabs(bound.appId, bound.env, state.value).ok) saveBlocked.value = true
}

function scheduleSave() {
  if (saveBlocked.value) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => { saveTimer = null; persist() }, SAVE_DEBOUNCE_MS)
}

onBeforeUnmount(persist)

const renamingId = ref(null)
const renameValue = ref('')
const renameInput = ref(null)

// 改名输入框写在 v-for 里，Vue 会把这种 string ref 收集成**数组**——直接 .focus() 会静默失败，
// 用户双击后还得再点一下才能打字。两种形状都吃下。
function focusRenameInput() {
  const el = Array.isArray(renameInput.value) ? renameInput.value[0] : renameInput.value
  el?.focus?.()
  el?.select?.()
}

// 换环境即换整套页签：dev 的脚本/页签/结果不会泄到 prod 屏幕上（US-06）。
watch(() => [props.appId, props.env], ([appId, env]) => {
  persist() // 先把当前这套还给原来的 (appId, env)
  bound = { appId, env }
  state.value = withRuntime(loadTabs(appId, env))
  renamingId.value = null
})

function selectTab(i) {
  if (i === state.value.active) return
  state.value.active = i
  persist() // 激活位也是工作现场的一部分
}

function createTab() {
  const next = addTab(state.value)
  if (!next) { message.warning(`最多 ${MAX_TABS} 个页签，请先关闭一些`); return }
  state.value = withRuntime(next)
  persist()
}

function closeTab(id) {
  const tab = state.value.tabs.find((t) => t && t.id === id)
  if (!tab) return
  // 空页签直接关：为“关掉一个什么都没有的页签”再弹一次确认是自找麻烦
  if (!tab.sql.trim() && !tab.results.length) { dropTab(id); return }
  Modal.confirm({
    title: '关闭页签',
    content: `「${tab.name}」里的脚本与结果会一并丢弃，确认关闭？`,
    okText: '关闭',
    okType: 'danger',
    onOk: () => dropTab(id),
  })
}

function dropTab(id) {
  // removeTab 保证关掉最后一个后自动补一个空白页签：不允许 0 页签死状态
  state.value = withRuntime(removeTab(state.value, id))
  persist()
}

function startRename(tab) {
  renamingId.value = tab.id
  renameValue.value = tab.name
  nextTick(focusRenameInput)
}

function commitRename() {
  const id = renamingId.value
  if (!id) return
  renamingId.value = null
  state.value = withRuntime(renameTab(state.value, id, renameValue.value))
  persist()
}

function cancelRename() { renamingId.value = null }

function run() {
  if (busy.value) return
  const list = splitSqlStatements(text.value).map((s) => s.sql)
  if (!list.length) { message.warning('没有可执行的语句'); return }
  // 一次批量只确认一次：整段清单摆出来，逐条确认会在多条写时把人问疯（Q5）。
  if (list.some((s) => !isReadOnlySelect(s))) {
    Modal.confirm({ title: '确认执行写操作', content: list.join(';\n'),
      okText: '执行', okType: 'danger', onOk: () => submit(list) })
    return
  }
  return submit(list)
}

// target 在调用时就锁定为**发起那一页**的 id：结果回来时即使人已切到别的页签、
// 甚至新建/重命名过页签（构造出的是一批新对象），也要按 id 找回来只写回原来那一页。
// 锁对象引用不行：结构操作走的是浅拷贝，旧引用会被状态树脱下变成孤儿。
function tabById(id) {
  return state.value.tabs.find((t) => t && t.id === id) ?? null
}

// 但 id 不是稳定的逻辑身份：dev 与 prod 的默认页签都叫 query_1，关掉最后一个页签
// 补上来的全新空白页也叫 query_1。只按 id 回写会把 dev 的结果糊到 prod 页上、
// 把一个已被丢弃的页签的结果塞给接手它名字的新页。故发起时盖一个单调递增的
// 运行令牌，回来时令牌对不上（同一 id 已被重建过）就直接丢弃这次结果。
// 令牌是运行态字段，落盘时和 results 一起被 store 的白名单削掉。
let runSeq = 0

async function submit(list, targetId = current.value?.id) {
  busy.value = true
  const started = tabById(targetId)
  const token = ++runSeq
  if (started) {
    started.runToken = token
    started.results = []
    started.sentCount = list.length // 失败/未执行的序号都以此为锚
  }
  try {
    const { results: rows } = (await dbSqlBatch(props.appId, props.env, list)).data
    const back = tabById(targetId)
    // 令牌对不上说明这个 id 现在属于另一个逻辑页（切过环境 / 关过重建过）：结果跟着原页一起丢掉。
    if (back && back.runToken === token) {
      back.results = rows
      back.sentCount = list.length
    }
  } catch (e) {
    message.error(e?.response?.data?.detail || '执行失败')
  } finally {
    busy.value = false
  }
}

defineExpose({
  text, results, busy, sentCount, summary, run, submit,
  state, current, saveBlocked, createTab, closeTab, selectTab, startRename, commitRename,
})
</script>

<template>
  <div class="zc-sql">
    <!-- 页签栏：一个页签 = 一份脚本 + 一批结果；双击改名，× 关闭 -->
    <div class="zc-sql-tabs">
      <div v-for="(t, i) in state.tabs" :key="t.id" class="zc-sql-tab"
           :class="{ 'zc-sql-tab--active': i === state.active }"
           @click="selectTab(i)" @dblclick="startRename(t)">
        <input v-if="renamingId === t.id" ref="renameInput" v-model="renameValue" class="zc-sql-tab-input"
               aria-label="重命名页签" @click.stop @dblclick.stop
               @keyup.enter="commitRename" @keyup.esc="cancelRename" @blur="commitRename" />
        <span v-else class="zc-sql-tab-name" :title="t.name">{{ t.name }}</span>
        <button class="zc-sql-tab-close" type="button" aria-label="关闭页签" @click.stop="closeTab(t.id)"><close-outlined /></button>
      </div>
      <button class="zc-sql-tab-add" type="button" aria-label="新建页签" @click="createTab"><plus-outlined /></button>
    </div>
    <!-- 存不下去只是存不下去：黄条说清后果，文本绝不碰（用户可能已经写了半小时） -->
    <div v-if="saveBlocked" class="zc-sql-storage-warn" role="alert">
      本地存储已满或不可用，本次不再自动保存；页签里的脚本仍在，请自行复制备份。
    </div>
    <SqlEditor v-model="text" :tables="tables" class="zc-sql-input" />
    <div class="zc-sql-tools">
      <a-button class="zc-sql-run" type="primary" :disabled="busy" @click="run"><play-circle-outlined />执行</a-button>
      <span class="zc-sql-hint">多条以分号分隔，按序逐条执行</span>
      <span class="zc-sql-env">当前环境：{{ env }}</span>
    </div>
    <!-- 结果区做成固定高度的面板：汇总条粘底常驻，不再随长表格滑到看不见 -->
    <div class="zc-sql-results">
      <div class="zc-sql-results-head">执行结果<span class="zc-sql-results-tab">· {{ current.name }}</span></div>
      <div class="zc-sql-results-body">
        <div v-if="!results.length" class="zc-sql-empty">{{ busy ? '执行中…' : '尚未执行' }}</div>
        <div v-for="(r, i) in results" :key="i" class="zc-sql-block"
             :class="{ 'zc-sql-block--error': r.status === 'error', 'zc-sql-block--skipped': r.status === 'skipped' }">
          <div class="zc-sql-block-head">
            <span class="zc-sql-block-no">第 {{ (r.index ?? i) + 1 }}/{{ sentCount }} 条</span>
            <span class="zc-sql-block-op">{{ r.op }}</span>
            <span v-if="r.duration_ms != null" class="zc-sql-block-cost">{{ r.duration_ms }}ms</span>
            <span v-if="r.status === 'skipped'" class="zc-sql-block-state">未执行</span>
            <span v-else-if="r.status === 'error'" class="zc-sql-block-state zc-sql-block-state--error">失败</span>
          </div>
          <pre class="zc-sql-block-text">{{ r.sql }}</pre>
          <!-- 失败条：PG 原文不加工不做行号定位，用 pre 留住换行；不编造结果体 -->
          <pre v-if="r.status === 'error'" class="zc-sql-error-text">{{ r.error }}</pre>
          <template v-else-if="r.kind === 'rows'">
            <a-table size="small" :dataSource="r.rows"
                     :columns="r.columns.map(c => ({ title: c, dataIndex: c }))" :pagination="false" />
            <div v-if="r.truncated" class="zc-sql-truncated">
              结果过大，仅显示前 {{ r.rows.length }} 行；请用 LIMIT / WHERE 收窄。
            </div>
          </template>
          <div v-else-if="r.kind === 'affected'" class="zc-sql-affected">影响 {{ r.affected }} 行</div>
        </div>
      </div>
      <div v-if="summary" class="zc-sql-summary">{{ summary }}</div>
    </div>
  </div>
</template>
