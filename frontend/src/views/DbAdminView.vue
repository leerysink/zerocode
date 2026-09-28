<script setup>
// 应用 DB 操作台（设计 2026-09-28）。骨架：顶栏 + dev/prod 切换 + prod 警示 + 左表树 + 结构页。
// 数据网格在 Task 10 填充，SQL 控制台/审计抽屉在 Task 11 填充。
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
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
const editRow = ref(null) // 当前编辑行（默认首行；点行切换）

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
  loadTables()
})

async function loadData() {
  if (!selected.value) return
  try {
    const { data } = await dbTableData(appId, env.value, selected.value,
      { page: page.value, size: size.value, sort: sortCol.value || undefined, dir: sortDir.value })
    rows.value = data.rows; dataCols.value = data.columns; total.value = data.total
    editRow.value = rows.value.length ? { ...rows.value[0] } : null
  } catch (e) { message.error(e?.response?.data?.detail || '数据加载失败') }
}
function onPageChange(p) { page.value = p; loadData() }
function onSort(col) {
  if (sortCol.value === col) sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  else { sortCol.value = col; sortDir.value = 'asc' }
  page.value = 1; loadData()
}
function pickRow(row) { editRow.value = { ...row } }
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
      try { await dbSqlExec(appId, env.value, sql); message.success('已保存'); await loadData() }
      catch (e) { message.error(e?.response?.data?.detail || '执行失败') }
    },
  })
}
watch(selected, () => { page.value = 1; editRow.value = null; loadData() })

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
function onAuditFilter() { fetchAudit() }
async function refresh() {
  await loadTables()
  if (selected.value) { await selectTable(selected.value); await loadData() }
}

onMounted(loadTables)
defineExpose({ selected, schema, selectTable, loadData, saveEdits, canEdit, openAudit, refresh, fetchAudit, auditEnv, auditOp })
</script>

<template>
  <div class="zc-db">
    <div class="zc-db-head">
      <h2>DB 操作 · 应用 {{ appId }}</h2>
      <div class="zc-db-head-right">
        <a-segmented v-model:value="env" class="zc-db-env" :options="[{ value: 'dev' }, { value: 'prod' }]" />
        <a-button class="zc-db-refresh" @click="refresh">刷新</a-button>
        <a-button class="zc-db-audit-btn" @click="openAudit">操作历史</a-button>
      </div>
    </div>
    <a-alert v-if="env === 'prod'" class="zc-db-prod-warn" type="warning" show-icon
             message="直改 prod 会绕过发布迁移轨，请确认你知道自己在做什么" />
    <div class="zc-db-body">
      <div class="zc-db-left">
        <a-tree class="zc-db-tree" :tree-data="tables.map(t => ({ key: t.name, title: t.name }))"
                @select="(k) => k[0] && selectTable(k[0])" />
      </div>
      <div class="zc-db-right">
        <a-tabs>
          <a-tab-pane key="data" tab="数据">
            <span v-if="!selected">选择左侧表以查看数据</span>
            <div v-else>
              <div v-if="!canEdit()" class="zc-db-edit-disabled">
                该表无主键，行内编辑已禁用（仍可浏览，改数据请用查询控制台）
              </div>
              <a-table size="small" :dataSource="rows" :rowKey="(r, i) => i"
                       :columns="dataCols.map(c => ({ title: c, dataIndex: c, sorter: true }))"
                       :pagination="{ total, current: page, pageSize: size, onChange: onPageChange }"
                       :custom-row="(r) => ({ onClick: () => pickRow(r) })"
                       @change="(_p, _f, s, extra) => extra && extra.action === 'sort' && onSort(s.columnKey || s.field)" />
              <div v-if="canEdit() && editRow" class="zc-db-edit-panel">
                <div class="zc-db-edit-title">编辑行（主键：{{ schema.primary_key.join(', ') }}）</div>
                <div v-for="c in dataCols" :key="c" class="zc-db-edit-field">
                  <label>{{ c }}</label>
                  <a-input :value="editRow[c]" :disabled="isPk(c)"
                           @change="(e) => editRow[c] = e.target.value" />
                </div>
                <button class="zc-db-edit-save" @click="saveEdits">保存</button>
              </div>
            </div>
          </a-tab-pane>
          <a-tab-pane key="schema" tab="结构">
            <a-table v-if="schema" size="small" rowKey="name" :dataSource="schema.columns"
                     :columns="[{ title: '列', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' },
                               { title: '默认值', dataIndex: 'default' }]" />
            <div v-if="schema && schema.primary_key.length" class="zc-db-pk">主键：{{ schema.primary_key.join(', ') }}</div>
          </a-tab-pane>
          <a-tab-pane key="query" tab="查询">
            <SqlConsole :app-id="appId" :env="env" :tables="tables" />
          </a-tab-pane>
        </a-tabs>
      </div>
    </div>
    <a-drawer v-model:open="auditing" title="操作历史" width="640">
      <div class="zc-db-audit-drawer">
        <div class="zc-db-audit-filter">
          <select v-model="auditEnv" class="zc-db-audit-env" @change="onAuditFilter">
            <option value="">全部环境</option>
            <option value="dev">dev</option>
            <option value="prod">prod</option>
          </select>
          <select v-model="auditOp" class="zc-db-audit-op" @change="onAuditFilter">
            <option value="">全部类型</option>
            <option value="select">select</option>
            <option value="insert">insert</option>
            <option value="update">update</option>
            <option value="delete">delete</option>
            <option value="ddl">ddl</option>
            <option value="other">other</option>
          </select>
        </div>
        <a-table size="small" rowKey="id" :dataSource="auditItems"
                 :columns="[{ title: '时间', dataIndex: 'ts' }, { title: '环境', dataIndex: 'env' },
                            { title: '类型', dataIndex: 'op_type' }, { title: '状态', dataIndex: 'status' },
                            { title: '耗时ms', dataIndex: 'duration_ms' }, { title: '目标表', dataIndex: 'target_table' },
                            { title: 'SQL', dataIndex: 'sql_text' }, { title: '错误', dataIndex: 'error' }]" />
      </div>
    </a-drawer>
  </div>
</template>

<style scoped>
.zc-db { padding: 16px 20px; height: 100vh; display: flex; flex-direction: column; box-sizing: border-box; }
.zc-db-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.zc-db-head h2 { font-size: 18px; margin: 0; }
.zc-db-head-right { display: flex; align-items: center; gap: 12px; }
.zc-db-audit-filter { display: flex; gap: 8px; margin-bottom: 12px; }
.zc-db-audit-filter select { padding: 4px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.zc-db-body { flex: 1; display: flex; gap: 12px; min-height: 0; }
.zc-db-left { width: 240px; overflow: auto; border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; }
.zc-db-right { flex: 1; overflow: auto; }
.zc-db-pk { margin-top: 8px; color: #6b7280; font-size: 12px; }
.zc-db-edit-panel { margin-top: 12px; padding: 12px; border: 1px solid #e5e7eb; border-radius: 6px; }
.zc-db-edit-title { font-size: 13px; color: #6b7280; margin-bottom: 8px; }
.zc-db-edit-field { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.zc-db-edit-field label { width: 120px; font-size: 13px; }
.zc-db-edit-save { margin-top: 8px; padding: 4px 14px; border: 1px solid #5b7a9d; border-radius: 4px;
                   background: #5b7a9d; color: #fff; cursor: pointer; }
</style>
