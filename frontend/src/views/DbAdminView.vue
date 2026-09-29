<script setup>
// 应用 DB 操作台（设计 2026-09-28）。骨架：顶栏 + dev/prod 切换 + prod 警示 + 左表树 + 结构页。
// 数据网格在 Task 10 填充，SQL 控制台/审计抽屉在 Task 11 填充。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { EditOutlined, HistoryOutlined, ReloadOutlined, SaveOutlined, TableOutlined } from '@ant-design/icons-vue'
import { dbTables, dbTableSchema, dbTableData, dbSqlExec, dbAudit } from '../api'
import SqlConsole from '../components/SqlConsole.vue'

const route = useRoute()
const appId = route.params.id
const env = ref('dev')
const tables = ref([])
const selected = ref(null)
const schema = ref(null)

// ---- 数据网格 + 行内编辑（Task 10）----
const rows = ref([])
const dataCols = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(50)
const sortCol = ref(null)
const sortDir = ref('asc')
const editRow = ref(null) // 弹框里的草稿副本（点行末编辑才填；关闭不重置，下次 openEdit 整份覆盖）
const editOpen = ref(false)

// 行号列：Navicat 式的第一列，跨页连续计数；纯展示，不带 sorter、不参与编辑。
// 行末「操作」列固定右侧：表一宽就横向滚动，编辑入口必须跟着行走而不是钉在页面底部。
const gridColumns = computed(() => [
  {
    title: '#', key: '__row_no', width: 56, align: 'right', className: 'zc-db-col-no',
    customRender: ({ index }) => (page.value - 1) * size.value + index + 1,
  },
  ...dataCols.value.map((c) => ({ title: c, dataIndex: c, sorter: true })),
  { title: '操作', key: '__actions', width: 88, fixed: 'right', align: 'center' },
])

// 状态栏右半句：未选表时说明白，不用「共 0 行」糊人。
const selectionText = computed(() => (selected.value
  ? `${selected.value} · 共 ${total.value} 行 · 第 ${page.value} 页`
  : '未选择表'))

async function loadTables() {
  try {
    tables.value = (await dbTables(appId, env.value)).data.tables
  } catch (e) {
    message.error(e?.response?.data?.detail || '表列表加载失败')
  }
}
async function selectTable(name) {
  selected.value = name
  try {
    schema.value = (await dbTableSchema(appId, env.value, name)).data
  } catch (e) {
    message.error(e?.response?.data?.detail || '表结构加载失败')
    schema.value = null
  }
}
watch(env, () => {
  selected.value = null; schema.value = null
  rows.value = []; dataCols.value = []; total.value = 0; editRow.value = null
  // 弹框开着时切环境，下表已经不属于这个库了，不给它留残影
  editOpen.value = false
  loadTables()
})

async function loadData() {
  if (!selected.value) return
  try {
    const { data } = await dbTableData(appId, env.value, selected.value,
      { page: page.value, size: size.value, sort: sortCol.value || undefined, dir: sortDir.value })
    rows.value = data.rows; dataCols.value = data.columns; total.value = data.total
    // 不再把首行塞进 editRow：没有显式点编辑就不该有一个看不见的“当前编辑行”
  } catch (e) { message.error(e?.response?.data?.detail || '数据加载失败') }
}
function onPageChange(p) { page.value = p; loadData() }
function onSort(col) {
  if (sortCol.value === col) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  else { sortCol.value = col; sortDir.value = 'asc' }
  page.value = 1; loadData()
}
function openEdit(row) {
  if (!canEdit()) return // 无主键表按钮已禁用，这里防绕过禁用态直接调用
  editRow.value = { ...row } // 编辑副本：改到一半取消，表格里的原值不应被动过
  editOpen.value = true
}
const canEdit = () => (schema.value?.primary_key || []).length > 0
const isPk = (c) => (schema.value?.primary_key || []).includes(c)
const q = (s) => `"${String(s).replace(/"/g, '""')}"`
const lit = (v) => (v === null || v === undefined) ? 'NULL'
  : typeof v === 'number' ? String(v) : `'${String(v).replace(/'/g, "''")}'`
function buildUpdate(row) {
  const pk = schema.value.primary_key
  const setCols = dataCols.value.filter((c) => !pk.includes(c))
  if (!setCols.length) return null // 全列即主键：无可编辑列，不生成坏 SQL
  const set = setCols.map((c) => `${q(c)} = ${lit(row[c])}`).join(', ')
  const where = pk.map((c) => `${q(c)} = ${lit(row[c])}`).join(' AND ')
  return `UPDATE ${q(selected.value)} SET ${set} WHERE ${where}`
}
function saveEdits() {
  if (!editRow.value) return
  const sql = buildUpdate(editRow.value)
  if (!sql) { message.warning('该表所有列均为主键，无可编辑列（请用查询控制台）'); return }
  Modal.confirm({
    title: '确认执行写操作', content: sql, okText: '执行', okType: 'danger',
    onOk: async () => {
      try { await dbSqlExec(appId, env.value, sql); message.success('已保存'); editOpen.value = false; await loadData() }
      catch (e) { message.error(e?.response?.data?.detail || '执行失败') }
    },
  })
}
watch(selected, () => { page.value = 1; editRow.value = null; editOpen.value = false; loadData() })

// ---- 查询控制台 + 审计抽屉 ----
// SQL 编辑器与批量执行已下沉到 SqlConsole（Ticket 002），本视图只负责供 appId/env/tables。
const auditing = ref(false)
const auditItems = ref([])
const auditEnv = ref('') // '' = 全部环境
const auditOp = ref('') // '' = 全部操作类型

async function openAudit() {
  auditing.value = true
  await fetchAudit()
}
async function fetchAudit() {
  const params = { limit: 100 }
  if (auditEnv.value) params.env = auditEnv.value
  if (auditOp.value) params.op = auditOp.value
  try { auditItems.value = (await dbAudit(appId, params)).data.items }
  catch (e) { message.error(e?.response?.data?.detail || '审计加载失败') }
}
// 过滤器由原生 select 换成 a-select：v-model 写回与 change 的先后顺序不该依赖组件实现，
// 直接 watch 两个 ref 重拉，边界（切回"全部"）也自然覆盖。
watch([auditEnv, auditOp], fetchAudit)
const auditEnvOptions = [
  { value: '', label: '全部环境' }, { value: 'dev', label: 'dev' }, { value: 'prod', label: 'prod' },
]
const auditOpOptions = ['', 'select', 'insert', 'update', 'delete', 'ddl', 'other']
  .map((v) => ({ value: v, label: v || '全部类型' }))
// 列宽定死 + 横向滚动：原来 937px 内容塞进 625px 容器，列被挤扁且必须横滚却看不见滚轮提示
const auditColumns = [
  { title: '时间', dataIndex: 'ts', width: 150 },
  { title: '环境', dataIndex: 'env', width: 72 },
  { title: '类型', dataIndex: 'op_type', width: 88 },
  { title: '状态', dataIndex: 'status', width: 84 },
  { title: '耗时ms', dataIndex: 'duration_ms', width: 84, align: 'right', className: 'zc-db-audit-num' },
  { title: '目标表', dataIndex: 'target_table', width: 130 },
  { title: 'SQL', dataIndex: 'sql_text', width: 320 },
  { title: '错误', dataIndex: 'error', width: 220 },
]

/** 审计时间列：后端给的是带微秒+时区的 ISO，压到秒。 */
function formatTs(ts) {
  if (ts === null || ts === undefined || ts === '') return '-'
  const m = String(ts).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/)
  return m ? `${m[1]} ${m[2]}` : String(ts) // 非 ISO 形状原样返回，不编造时间
}

/** 空值占位：0 / false 是合法值不能被吞成 '-'，只有 null / undefined / 空串算空。 */
function cellText(v) {
  return v === null || v === undefined || v === '' ? '-' : String(v)
}
async function refresh() {
  await loadTables()
  if (selected.value) { await selectTable(selected.value); await loadData() }
}

onMounted(loadTables)
defineExpose({ selected, schema, selectTable, loadData, saveEdits, canEdit, openAudit, refresh, fetchAudit,
  auditEnv, auditOp, selectionText, formatTs, cellText, editRow, editOpen, openEdit })
</script>

<template>
  <div class="zc-db" :class="{ 'is-prod': env === 'prod' }">
    <div class="zc-db-head">
      <h2>DB 操作 · 应用 {{ appId }}</h2>
      <div class="zc-db-head-right">
        <a-segmented v-model:value="env" class="zc-db-env" :options="[{ value: 'dev' }, { value: 'prod' }]" />
        <a-button class="zc-db-refresh" @click="refresh"><reload-outlined />刷新</a-button>
        <a-button class="zc-db-audit-btn" @click="openAudit"><history-outlined />操作历史</a-button>
      </div>
    </div>
    <a-alert v-if="env === 'prod'" class="zc-db-prod-warn" type="warning" show-icon
             message="直改 prod 会绕过发布迁移轨，请确认你知道自己在做什么" />
    <div class="zc-db-body">
      <div class="zc-db-left">
        <div class="zc-db-left-head">
          <span>表</span>
          <span class="zc-db-left-count">{{ tables.length }}</span>
        </div>
        <a-tree v-if="tables.length" class="zc-db-tree" show-icon
                :tree-data="tables.map(t => ({ key: t.name, title: t.name }))"
                @select="(k) => k[0] && selectTable(k[0])">
          <template #icon><table-outlined /></template>
        </a-tree>
        <div v-else class="zc-db-left-empty">该库暂无表</div>
      </div>
      <div class="zc-db-right">
        <a-tabs>
          <a-tab-pane key="data" tab="数据">
            <div v-if="!selected" class="zc-db-placeholder">选择左侧表以查看数据</div>
            <div v-else class="zc-db-tab-body">
              <div v-if="!canEdit()" class="zc-db-edit-disabled">
                该表无主键，行内编辑已禁用（仍可浏览，改数据请用查询控制台）
              </div>
              <a-table size="small" :dataSource="rows" :rowKey="(r, i) => i"
                       :columns="gridColumns"
                       :pagination="{ total, current: page, pageSize: size, showSizeChanger: false, onChange: onPageChange }"
                       :scroll="{ x: 'max-content' }"
                       @change="(_p, _f, s, extra) => extra && extra.action === 'sort' && onSort(s.columnKey || s.field)">
                <!-- antd v4 先跑 column.customRender，再用 customRenderSlot 包一层 #bodyCell：
                     插槽返回的全是注释（v-if 未命中）时会回落到 customRender / 原值，
                     所以这里只处理 __actions，不必也不该把行号列与数据列接过来。 -->
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === '__actions'">
                    <span class="zc-db-row-edit-wrap"
                          :title="canEdit() ? '编辑该行' : '该表无主键，无法按行定位，编辑已禁用'">
                      <button class="zc-db-row-edit" :disabled="!canEdit()" @click.stop="openEdit(record)">
                        <edit-outlined />编辑
                      </button>
                    </span>
                  </template>
                </template>
              </a-table>
              <a-modal v-model:open="editOpen" :title="`编辑行 · ${selected}`" :width="760"
                       wrap-class-name="zc-db-edit-modal" :mask-closable="false">
                <div v-if="editRow" class="zc-db-edit-fields">
                  <div v-for="c in dataCols" :key="c" class="zc-db-edit-field">
                    <label>{{ c }}</label>
                    <a-input :value="editRow[c]" :disabled="isPk(c)"
                             @change="(e) => editRow[c] = e.target.value" />
                  </div>
                </div>
                <template #footer>
                  <button class="zc-db-edit-cancel" @click="editOpen = false">取消</button>
                  <button class="zc-db-edit-save" @click="saveEdits"><save-outlined />保存</button>
                </template>
              </a-modal>
            </div>
          </a-tab-pane>
          <a-tab-pane key="schema" tab="结构">
            <div v-if="!schema" class="zc-db-placeholder">选择左侧表以查看结构</div>
            <div v-else class="zc-db-tab-body">
              <a-table size="small" rowKey="name" :dataSource="schema.columns" :pagination="false"
                       :scroll="{ x: 'max-content' }"
                       :columns="[{ title: '列', dataIndex: 'name', width: 180 }, { title: '类型', dataIndex: 'type', width: 200 },
                                 { title: '默认值', dataIndex: 'default' }]" />
              <div v-if="schema.primary_key.length" class="zc-db-pk">主键：{{ schema.primary_key.join(', ') }}</div>
            </div>
          </a-tab-pane>
          <a-tab-pane key="query" tab="查询">
            <div class="zc-db-tab-console">
              <SqlConsole :app-id="appId" :env="env" :tables="tables" />
            </div>
          </a-tab-pane>
        </a-tabs>
      </div>
    </div>
    <a-drawer v-model:open="auditing" title="操作历史" width="900" root-class-name="zc-db-drawer">
      <div class="zc-db-audit-drawer">
        <div class="zc-db-audit-filter">
          <span class="zc-db-audit-filter-label">环境</span>
          <a-select v-model:value="auditEnv" class="zc-db-audit-env" :options="auditEnvOptions"
                    popup-class-name="zc-db-dd" />
          <span class="zc-db-audit-filter-label">类型</span>
          <a-select v-model:value="auditOp" class="zc-db-audit-op" :options="auditOpOptions"
                    popup-class-name="zc-db-dd" />
        </div>
        <div class="zc-db-audit-body">
          <a-table size="small" rowKey="id" :dataSource="auditItems" :columns="auditColumns"
                   :pagination="false" :scroll="{ x: 'max-content' }">
            <template #bodyCell="{ column, record, text }">
              <span v-if="column.dataIndex === 'ts'" class="zc-db-audit-time">{{ formatTs(record.ts) }}</span>
              <a-tag v-else-if="column.dataIndex === 'status'" class="zc-db-audit-status"
                     :color="record.status === 'success' ? 'success' : 'error'">{{ record.status === 'success' ? '成功' : '失败' }}</a-tag>
              <span v-else-if="column.dataIndex === 'sql_text'" class="zc-db-audit-sql">{{ cellText(record.sql_text) }}</span>
              <span v-else-if="column.dataIndex === 'error'" class="zc-db-audit-error">{{ cellText(record.error) }}</span>
              <span v-else :class="{ 'zc-db-audit-null': cellText(text) === '-' }">{{ cellText(text) }}</span>
            </template>
          </a-table>
        </div>
      </div>
    </a-drawer>
    <div class="zc-db-status">
      <span class="zc-db-status-env">{{ env }}</span>
      <span>表 {{ tables.length }} 张</span>
      <span class="zc-db-status-sep">|</span>
      <span>{{ selectionText }}</span>
    </div>
  </div>
</template>
