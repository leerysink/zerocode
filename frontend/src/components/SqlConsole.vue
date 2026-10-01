<script setup>
// 查询控制台：多页签，每页签一套脚本与一批结果，**遇错即停**且失败可见（Ticket 003/004/005/009）。
//
// 后端在同一连接内按序执行，某条报错则该条 status='error'、其后所有可执行条
// status='skipped' 并立即停止，已成功的条目**不回滚**（ADR 0007）。
//
// 页签（Ticket 005 / ADR 0008）：
// - 一个页签持有自己的 SQL 文本与结果块列表；结果只活在内存，刷新后为空（US-05）。
// - 页签按 (appId, env) 分区落 localStorage，切环境 = 切整套页签（替掉原「切 env 清 sqlText」）。
// - 写入失败（配额满 / 存储不可用）→ 黄条提示且本次会话不再尝试写入，编辑器文本绝不让路。
//
// 结果区（Ticket 009）：一条语句一个结果页签，纵向堆叠换成水平导航，DOM 里同一时刻只有
// 激活那一块；「停在第几页」也是运行态，跟 results 一起挂在页签对象上，落盘前被削掉。
//
// 选区与格式化（Ticket 006）：选区只在这里参与“取哪一段去执行”，不改写用户脚本；
// 格式化的算法在 utils/sqlFormat.js（纯函数，能脱离编辑器单测），这里只负责读数与写回。
//
// 确认弹框（Ticket 007）：一次点击算出一张执行计划（utils/sqlClassify.planExecution），
// 纯读段直接执行不拦人；含写/DDL 就弹逐条清单（op 徽章 + 高危标红），prod 还要手打确认词。
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { CloseOutlined, PlayCircleOutlined, PlusOutlined } from '@ant-design/icons-vue'
import { dbSqlBatch, dbTableSchema } from '../api'
import SqlEditor from './SqlEditor.vue'
import { splitSqlStatements } from '../utils/sqlSplit'
import { planExecution } from '../utils/sqlClassify'
import { createCompletionContext } from '../utils/sqlCompletion'
import { formatRange } from '../utils/sqlFormat'
import { MAX_TABS, addTab, loadTabs, removeTab, renameTab, saveTabs } from '../utils/sqlTabsStore'

const props = defineProps({
  appId: { type: [String, Number], required: true },
  env: { type: String, required: true },
  tables: { type: Array, default: () => [] },
})

// ---- 补全上下文（Ticket 008A / US-15）
// 只造一次、引用永久稳定：SqlEditor 的扩展链在 onMounted 里一次建好，换环境时另造一个
// 就得重建整条 CodeMirror（光标与撤销栈一起没了），所以 env / appId / 表清单都是原地更新。
// 表名复用 DbAdminView 已经拉好的那份 tables，不新发请求；列名要等真打了 `.` 才按表懒拉，
// 拉失败在 sqlCompletion 里静默降级成“没候选”，不打扰也不报错。
const completion = createCompletionContext({
  appId: props.appId,
  env: props.env,
  tables: props.tables,
  fetchSchema: (appId, env, table) => dbTableSchema(appId, env, table),
})
watch(() => props.appId, (value) => completion.setAppId(value))
watch(() => props.env, (value) => completion.setEnv(value))
watch(() => props.tables, (value) => completion.setTables(value))

// 页签对象在内存里多带几个运行时字段（results / sentCount / resultIndex / runToken），落盘时由
// store 的白名单削掉——运行态与持久态共用一个对象，省掉一份“id → 结果”的旁路映射。
function withRuntime(s) {
  for (const t of s.tabs) {
    if (!Array.isArray(t.results)) t.results = []
    if (!Number.isInteger(t.sentCount)) t.sentCount = 0
    if (!Number.isInteger(t.resultIndex)) t.resultIndex = 0
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
// 编辑器句柄：选区与写回都从这里拿。DbAdminView.test.js 那类退化的纯 textarea 桩上这些方法
// 不存在，全部走 `?.()` + 回落全段，不让“没选区通道”把执行路径直接敲坏。
const editorRef = ref(null)
// 块头分母用**发出去的条数**而不是 results.length：服务端会丢弃纯注释段，
// 结果集的 index 是原数组下标（可能稀疏），用 length 会显示成「第 3/1 条」。
const sentCount = computed(() => current.value.sentCount)
const busy = ref(false)

// 结果页签的落点在**渲染层**钳制：越界值（重执行后结果变少、外部把 index 写飞）只会让
// activeResult 回落到 0，不会把面板渲染成空白——存储位本身不动，切回结果变多时还能回去。
const activeResult = computed(() => {
  const i = current.value.resultIndex
  return Number.isInteger(i) && i >= 0 && i < results.value.length ? i : 0
})
const activeBlock = computed(() => results.value[activeResult.value] ?? null)

// 点结果页签只是**看**另一条结果，不是把工作现场换了：不写 localStorage（009 裁定 2）。
// 落点已挂在页签对象上，切脚本页签时各归各页，不串台。
function selectResult(i) {
  const tab = current.value
  if (!tab) return
  tab.resultIndex = i
}

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

// ---- 执行确认弹框（Ticket 007 / US-12、US-13）----
// 002 那会儿是裸 Modal.confirm 把整段拼成一个 content：能确认，但看不出“哪条是写、哪条要命”。
// 这里换成逐条清单。确认门槛取严（宁可多问一次），徽章取与后端一致，两者故意不同：
// `SELECT setval('s',1)` 就是“徽章 select + 需要确认 + prod 要输确认词”的组合，不要当 bug 改掉。
const PREVIEW_LIMIT = 160
const RISK_TEXT = {
  no_where: '无 WHERE 的 UPDATE/DELETE，将作用于全表',
  ddl: 'DDL，将改动表结构',
}
const confirmOpen = ref(false)
const execPlan = ref(null)
const phrase = ref('')
const expanded = ref({})
// 确认期间人可能切了页签：发起时就把目标页钉住，别把这一段的 SQL 落到另一页的结果区。
let confirmTargetId = null

// 确认词只认字面 prod：大小写不对、前后带空格都不算输对——
// 要的就是“亲手把环境名打一遍”这点阻力，不是做一个聪明的输入校验。
const phraseOk = computed(() => !execPlan.value?.requirePhrase || phrase.value === 'prod')
const riskCount = computed(() => (execPlan.value?.items ?? []).filter((i) => i.risk.length).length)
// 标题先把“几条 / 哪个环境 / 有几条高危”说清，不用展开清单就知道这次点下去是什么量级。
const confirmTitle = computed(() => {
  const n = execPlan.value?.items?.length ?? 0
  const risk = riskCount.value
  return `确认执行 ${n} 条语句（${props.env}）${risk ? ` · ${risk} 条高危` : ''}`
})

function openConfirm(p) {
  execPlan.value = p
  phrase.value = ''
  expanded.value = {}
  confirmTargetId = current.value?.id ?? null
  confirmOpen.value = true
}

function previewText(sql, i) {
  if (expanded.value[i] || sql.length <= PREVIEW_LIMIT) return sql
  return `${sql.slice(0, PREVIEW_LIMIT)}…`
}
const expandable = (sql) => sql.length > PREVIEW_LIMIT
function toggleExpand(i) { expanded.value[i] = !expanded.value[i] }
// 认不出的标记原样显示：将来 riskFlags 加新种类时，这里不要静默把风险说明吞掉
const riskText = (risk) => risk.map((r) => RISK_TEXT[r] || r).join('；')

function doConfirm() {
  // 双保险：disabled 挡的是弹框上的按钮，这里再挡一次绕过按钮的调用（以及回车连点）
  if (!execPlan.value || !phraseOk.value) return
  const list = execPlan.value.items.map((i) => i.sql)
  confirmOpen.value = false
  return submit(list, confirmTargetId ?? undefined)
}

function run() {
  if (busy.value) return
  const stmts = splitSqlStatements(execSource())
  if (!stmts.length) { message.warning('没有可执行的语句'); return }
  const plan = planExecution(stmts, props.env)
  // 纯只读段直接执行：为 SELECT 弹一次确认只会让人学会“无脑点确定”，那比不弹更危险。
  if (!plan.needConfirm) return submit(plan.items.map((i) => i.sql))
  // 一次批量只确认一次：逐条确认会在多条写时把人问疯（Q5），清单式弹框两头都占到。
  openConfirm(plan)
}

// 执行段取“选区优先，否则全段”（US-10）：判定只看“选区文本是不是空字串”——
// 真实编辑器里光标塌点时 sliceDoc 就是空串，那才是“没选”；选中一段空白不是，
// 它就照原样交给拆分器，拆不出可执行语句就走「没有可执行的语句」提示，
// 而不是悄悄把用户没选的那些也跑了（静默扩大执行范围比多一次点击危险得多）。
function execSource() {
  const sel = editorRef.value?.getSelectionText?.()
  return typeof sel === 'string' && sel !== '' ? sel : text.value
}

// 格式化（US-02）：算法全在 formatRange 里（选区交换/钳制、剔首尾空白、抛错回退），
// 这里只决定“能不能写、写不写得回去”：
// - 没成功（空选区 / 解析抛错）→ 一次写回也不发，只给提示，原文一个字不让改；
// - 有写回通道→ 就地替换 [from, to) 并把选区新位置递过去（否则整段重排会把光标丢回文首）；
// - 只有 v-model 的退化桩→ 直接换整段文本，保证格式化不依赖编辑器能力也能用。
function formatSql() {
  const ed = editorRef.value
  const range = ed?.getSelectionRange?.() ?? null
  const res = formatRange(text.value, range?.from ?? null, range?.to ?? null)
  if (!res.ok) {
    // 解析不了是用户写的东西有问题（用 warning）；无事可做只是点了个空段（用 info）
    if (res.kind === 'error') message.warning('这段 SQL 解析不了，已保持原文')
    else message.info('没有可格式化的内容')
    return
  }
  if (typeof ed?.replaceRange === 'function') {
    ed.replaceRange(res.from, res.to, res.inserted,
      res.selStart != null ? { start: res.selStart, end: res.selEnd } : undefined)
    return
  }
  text.value = res.text
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

// 执行回来默认落在第一个失败页签（009 裁定 3），全成功落第 1 条：不用点就知道哪条炸了。
// 用的是**数组位**而不是结果里的 index——index 可能稀疏（后端丢纯注释段），数组位才是页签顺序。
function pickResultStart(rows) {
  const at = Array.isArray(rows) ? rows.findIndex((r) => r && r.status === 'error') : -1
  return at === -1 ? 0 : at
}

async function submit(list, targetId = current.value?.id) {
  busy.value = true
  const started = tabById(targetId)
  const token = ++runSeq
  if (started) {
    started.runToken = token
    started.results = []
    started.sentCount = list.length // 失败/未执行的序号都以此为锚
    started.resultIndex = 0 // 新一轮先回第 1 页，结果回来后再按落点规则定位
  }
  try {
    const { results: rows } = (await dbSqlBatch(props.appId, props.env, list)).data
    const back = tabById(targetId)
    // 令牌对不上说明这个 id 现在属于另一个逻辑页（切过环境 / 关过重建过）：结果跟着原页一起丢掉。
    if (back && back.runToken === token) {
      back.results = rows
      back.sentCount = list.length
      back.resultIndex = pickResultStart(rows)
    }
  } catch (e) {
    message.error(e?.response?.data?.detail || '执行失败')
  } finally {
    busy.value = false
  }
}

defineExpose({
  text, results, busy, sentCount, summary, run, submit, execSource, formatSql,
  confirmOpen, execPlan, phrase, phraseOk, confirmTitle, riskCount, doConfirm, toggleExpand,
  completion,
  state, current, activeResult, selectResult, saveBlocked, createTab, closeTab, selectTab, startRename, commitRename,
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
    <SqlEditor ref="editorRef" v-model="text" :completion="completion" class="zc-sql-input"
               @execute="run" @format="formatSql" />
    <div class="zc-sql-tools">
      <a-button class="zc-sql-run" type="primary" :disabled="busy" title="Ctrl+Enter" @click="run"><play-circle-outlined />执行</a-button>
      <a-button class="zc-sql-format" :disabled="busy" title="Ctrl+Shift+F" @click="formatSql">格式化</a-button>
      <span class="zc-sql-hint">多条以分号分隔，按序逐条执行；选中则只执行选区</span>
      <span class="zc-sql-env">当前环境：{{ env }}</span>
    </div>
    <!-- 结果区做成固定高度的面板：汇总条粘底常驻，不再随长表格滑到看不见 -->
    <div class="zc-sql-results">
      <div class="zc-sql-results-head">执行结果<span class="zc-sql-results-tab">· {{ current.name }}</span></div>
      <!-- 结果页签：一条语句一页，只有一条时不渲染页签栏（009 裁定 4）。无 ×：它只是导航，不行使「关闭工作现场」的语义 -->
      <div v-if="results.length > 1" class="zc-sql-rtabs" role="tablist" aria-label="语句结果页签">
        <button v-for="(r, i) in results" :key="i" type="button" role="tab" class="zc-sql-rtab"
                :class="{ 'zc-sql-rtab--active': i === activeResult, 'zc-sql-rtab--error': r.status === 'error', 'zc-sql-rtab--skipped': r.status === 'skipped' }"
                :aria-selected="i === activeResult ? 'true' : 'false'" :title="r.sql"
                @click="selectResult(i)">
          <span class="zc-sql-rtab-no">第 {{ (r.index ?? i) + 1 }}/{{ sentCount }} 条</span>
          <span class="zc-sql-rtab-op">{{ r.op }}</span>
          <span v-if="r.status === 'skipped'" class="zc-sql-rtab-state">未执行</span>
          <span v-else-if="r.status === 'error'" class="zc-sql-rtab-state zc-sql-rtab-state--error">失败</span>
        </button>
      </div>
      <div class="zc-sql-results-body">
        <div v-if="!results.length" class="zc-sql-empty">{{ busy ? '执行中…' : '尚未执行' }}</div>
        <!-- 同一时刻只渲染激活那一块（:key 让切页签时表格的内部状态跟着重置） -->
        <div v-else class="zc-sql-block" :key="activeResult"
             :class="{ 'zc-sql-block--error': activeBlock.status === 'error', 'zc-sql-block--skipped': activeBlock.status === 'skipped' }">
          <div class="zc-sql-block-head">
            <span class="zc-sql-block-no">第 {{ (activeBlock.index ?? activeResult) + 1 }}/{{ sentCount }} 条</span>
            <span class="zc-sql-block-op">{{ activeBlock.op }}</span>
            <span v-if="activeBlock.duration_ms != null" class="zc-sql-block-cost">{{ activeBlock.duration_ms }}ms</span>
            <span v-if="activeBlock.status === 'skipped'" class="zc-sql-block-state">未执行</span>
            <span v-else-if="activeBlock.status === 'error'" class="zc-sql-block-state zc-sql-block-state--error">失败</span>
          </div>
          <pre class="zc-sql-block-text">{{ activeBlock.sql }}</pre>
          <!-- 失败条：PG 原文不加工不做行号定位，用 pre 留住换行；不编造结果体 -->
          <pre v-if="activeBlock.status === 'error'" class="zc-sql-error-text">{{ activeBlock.error }}</pre>
          <template v-else-if="activeBlock.kind === 'rows'">
            <a-table size="small" :dataSource="activeBlock.rows"
                     :columns="activeBlock.columns.map(c => ({ title: c, dataIndex: c }))" :pagination="false" />
            <div v-if="activeBlock.truncated" class="zc-sql-truncated">
              结果过大，仅显示前 {{ activeBlock.rows.length }} 行；请用 LIMIT / WHERE 收窄。
            </div>
          </template>
          <div v-else-if="activeBlock.kind === 'affected'" class="zc-sql-affected">影响 {{ activeBlock.affected }} 行</div>
        </div>
      </div>
      <div v-if="summary" class="zc-sql-summary">{{ summary }}</div>
    </div>
    <!-- 逐条清单式确认（US-12/US-13）：一次点击只问一遍，但每一条都得看得见——
         裸 Modal.confirm 把整段拼成一个字符串，多条写混在一起等于没列。 -->
    <a-modal v-model:open="confirmOpen" wrap-class-name="zc-sql-confirm-modal"
             :title="confirmTitle" ok-text="执行" cancel-text="取消"
             :ok-button-props="{ disabled: !phraseOk }" :mask-closable="false" @ok="doConfirm">
      <ol class="zc-sql-confirm-list">
        <li v-for="(it, i) in (execPlan?.items ?? [])" :key="i" class="zc-sql-confirm-item"
            :class="{ 'zc-sql-confirm-item--risk': it.risk.length }">
          <div class="zc-sql-confirm-meta">
            <span class="zc-sql-confirm-no">第 {{ i + 1 }} 条</span>
            <span class="zc-sql-confirm-op">{{ it.op }}</span>
            <!-- startLine 是拆分器给的 1 基起始行，不能再 +1（那样每条都往后偏一行） -->
            <span v-if="it.startLine != null" class="zc-sql-confirm-at">行 {{ it.startLine }}</span>
            <button v-if="expandable(it.sql)" type="button" class="zc-sql-confirm-toggle"
                    :aria-expanded="expanded[i] ? 'true' : 'false'" @click="toggleExpand(i)">
              {{ expanded[i] ? '收起' : '展开' }}
            </button>
          </div>
          <pre class="zc-sql-confirm-sql">{{ previewText(it.sql, i) }}</pre>
          <div v-if="it.risk.length" class="zc-sql-confirm-risk">{{ riskText(it.risk) }}</div>
        </li>
      </ol>
      <!-- 确认词只在 prod 且这批里有非只读时出现：认不写字面 prod 就点不动「执行」 -->
      <div v-if="execPlan?.requirePhrase" class="zc-sql-confirm-phrase">
        <span class="zc-sql-confirm-phrase-tip">
          正在 prod 环境执行写操作，输入环境名 prod 以确认。
        </span>
        <a-input v-model:value="phrase" class="zc-sql-confirm-phrase-input" placeholder="prod"
                 aria-label="确认词" />
      </div>
    </a-modal>
  </div>
</template>
