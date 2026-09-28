<script setup>
// 应用 DB 操作台（设计 2026-09-28）。骨架：顶栏 + dev/prod 切换 + prod 警示 + 左表树 + 结构页。
// 数据网格在 Task 10 填充，SQL 控制台/审计抽屉在 Task 11 填充。
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { dbTables, dbTableSchema, dbTableData, dbSqlExec, dbAudit } from '../api'

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
watch(env, () => { selected.value = null; schema.value = null; loadTables() })

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
  const set = setCols.map((c) => `${q(c)} = ${lit(row[c])}`).join(', ')
  const where = pk.map((c) => `${q(c)} = ${lit(row[c])}`).join(' AND ')
  return `UPDATE ${q(selected.value)} SET ${set} WHERE ${where}`
}
function saveEdits() {
  if (!editRow.value) return
  const sql = buildUpdate(editRow.value)
  Modal.confirm({
    title: '确认执行写操作', content: sql, okText: '执行', okType: 'danger',
    onOk: async () => {
      try { await dbSqlExec(appId, env.value, sql); message.success('已保存'); await loadData() }
      catch (e) { message.error(e?.response?.data?.detail || '执行失败') }
    },
  })
}
watch(selected, () => { page.value = 1; editRow.value = null; loadData() })

// ---- SQL 控制台 + 审计抽屉（Task 11）----
const sqlText = ref('')
const sqlResult = ref(null)
const auditing = ref(false)
const auditItems = ref([])

function isSelect(s) { return /^\s*select\b/i.test(s) }
async function runRaw(s) {
  try { sqlResult.value = (await dbSqlExec(appId, env.value, s)).data }
  catch (e) { message.error(e?.response?.data?.detail || '执行失败') }
}
function runSql() {
  const s = sqlText.value.trim()
  if (!s) return
  if (isSelect(s)) return runRaw(s)
  Modal.confirm({ title: '确认执行写操作', content: s, okText: '执行', okType: 'danger',
    onOk: () => runRaw(s) })
}
async function openAudit() {
  auditing.value = true
  try { auditItems.value = (await dbAudit(appId, { limit: 100 })).data.items }
  catch (e) { message.error(e?.response?.data?.detail || '审计加载失败') }
}

onMounted(loadTables)
defineExpose({ selected, schema, selectTable, loadData, saveEdits, canEdit, runSql, openAudit })
</script>

<template>
  <div class="zc-db">
    <div class="zc-db-head">
      <h2>DB 操作 · 应用 {{ appId }}</h2>
      <div class="zc-db-head-right">
        <a-segmented v-model:value="env" class="zc-db-env" :options="[{ value: 'dev' }, { value: 'prod' }]" />
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
            <div v-else-if="!canEdit()" class="zc-db-edit-disabled">
              该表无主键，行内编辑已禁用（请用查询控制台）
            </div>
            <div v-else>
              <a-table size="small" :dataSource="rows" rowKey="__rk"
                       :columns="dataCols.map(c => ({ title: c, dataIndex: c, sorter: true }))"
                       :pagination="{ total, current: page, pageSize: size, onChange: onPageChange }"
                       @change="(_p, _f, s) => s && onSort(s.columnKey || s.field)" />
              <div v-if="editRow" class="zc-db-edit-panel">
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
            <a-textarea v-model:value="sqlText" class="zc-db-sql-input" :rows="4"
                        placeholder="输入 SQL，SELECT 直接执行，写/DDL 二次确认" />
            <a-button class="zc-db-sql-run" type="primary" @click="runSql">执行</a-button>
            <a-table v-if="sqlResult && sqlResult.kind === 'rows'" size="small" :dataSource="sqlResult.rows"
                     :columns="sqlResult.columns.map(c => ({ title: c, dataIndex: c }))" />
            <div v-else-if="sqlResult && sqlResult.kind === 'affected'" class="zc-db-sql-affected">
              影响 {{ sqlResult.affected }} 行
            </div>
          </a-tab-pane>
        </a-tabs>
      </div>
    </div>
    <a-drawer v-model:open="auditing" title="操作历史" width="640">
      <div class="zc-db-audit-drawer">
        <a-table size="small" rowKey="id" :dataSource="auditItems"
                 :columns="[{ title: '时间', dataIndex: 'ts' }, { title: '环境', dataIndex: 'env' },
                            { title: '类型', dataIndex: 'op_type' }, { title: '状态', dataIndex: 'status' },
                            { title: 'SQL', dataIndex: 'sql_text' }]" />
      </div>
    </a-drawer>
  </div>
</template>

<style scoped>
.zc-db { padding: 16px 20px; height: 100vh; display: flex; flex-direction: column; box-sizing: border-box; }
.zc-db-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.zc-db-head h2 { font-size: 18px; margin: 0; }
.zc-db-head-right { display: flex; align-items: center; gap: 12px; }
.zc-db-sql-input { margin-bottom: 8px; font-family: monospace; }
.zc-db-sql-run { margin-bottom: 12px; }
.zc-db-sql-affected { margin-top: 12px; color: #3f7d4e; font-size: 13px; }
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
