<script setup>
// 查询控制台：一个框、多条语句、多个结果块，**遇错即停**且失败可见（Ticket 003）。
//
// 后端在同一连接内按序执行，某条报错则该条 status='error'、其后所有可执行条
// status='skipped' 并立即停止，已成功的条目**不回滚**（ADR 0007）。
// 假分号状态机（004）、多页签与本地保存（005）、格式化与选区优先执行（006）、
// prod 确认词（007）都在后续票里长出来。
import { computed, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { dbSqlBatch } from '../api'
import SqlEditor from './SqlEditor.vue'
import { splitSqlStatements } from '../utils/sqlSplit'
import { isReadOnlySelect } from '../utils/sqlClassify'

const props = defineProps({
  appId: { type: [String, Number], required: true },
  env: { type: String, required: true },
  tables: { type: Array, default: () => [] },
})

const text = ref('')
const results = ref([])
const busy = ref(false)
// 块头分母用**发出去的条数**而不是 results.length：服务端会丢弃纯注释段，
// 结果集的 index 是原数组下标（可能稀疏），用 length 会显示成「第 3/1 条」。
const sentCount = ref(0)

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

// 换环境即丢上一批结果：不把 dev 的结果糊在 prod 页面上（脚本本身按环境各自保存在 005）。
watch(() => props.env, () => { results.value = []; sentCount.value = 0 })

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

async function submit(list) {
  busy.value = true
  results.value = []
  sentCount.value = list.length // 失败/未执行的序号都以此为锚
  try {
    results.value = (await dbSqlBatch(props.appId, props.env, list)).data.results
  } catch (e) {
    message.error(e?.response?.data?.detail || '执行失败')
  } finally {
    busy.value = false
  }
}

defineExpose({ text, results, busy, sentCount, summary, run, submit })
</script>

<template>
  <div class="zc-sql">
    <SqlEditor v-model="text" :tables="tables" class="zc-sql-input" />
    <div class="zc-sql-tools">
      <a-button class="zc-sql-run" type="primary" :disabled="busy" @click="run">执行</a-button>
      <span class="zc-sql-hint">多条以分号分隔，按序逐条执行</span>
      <span class="zc-sql-env">当前环境：{{ env }}</span>
    </div>
    <div v-for="(r, i) in results" :key="i" class="zc-sql-block"
         :class="{ 'zc-sql-block--error': r.status === 'error', 'zc-sql-block--skipped': r.status === 'skipped' }">
      <div class="zc-sql-block-head">
        <span class="zc-sql-block-no">第 {{ (r.index ?? i) + 1 }}/{{ sentCount }} 条</span>
        <span class="zc-sql-block-op">{{ r.op }}</span>
        <span v-if="r.duration_ms != null" class="zc-sql-block-cost">{{ r.duration_ms }}ms</span>
        <span v-if="r.status === 'skipped'" class="zc-sql-block-state">未执行</span>
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
    <div v-if="summary" class="zc-sql-summary">{{ summary }}</div>
  </div>
</template>

<style scoped>
.zc-sql-tools { display: flex; align-items: center; gap: 12px; margin: 8px 0 12px; }
.zc-sql-hint, .zc-sql-env { color: #6b7280; font-size: 12px; }
.zc-sql-block { border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
.zc-sql-block--error { border-color: #e7bcbc; background: #fdf6f6; }
.zc-sql-block--skipped { border-color: #e8ebef; background: #f7f8fa; }
.zc-sql-block-head { display: flex; gap: 8px; align-items: center; font-size: 12px; color: #6b7280; }
.zc-sql-block-op { padding: 0 6px; border-radius: 4px; background: #eef3f8; color: #2f6fa7; }
.zc-sql-block-state { padding: 0 6px; border-radius: 4px; background: #eceff3; color: #7b8794; }
.zc-sql-block-text { margin: 6px 0 8px; font-family: ui-monospace, Consolas, monospace;
                     font-size: 12px; color: #3f5061; white-space: pre-wrap; }
.zc-sql-error-text { margin: 0; padding: 6px 8px; border-radius: 4px; background: #fbeeee;
                     color: #b96a6a; font-family: ui-monospace, Consolas, monospace;
                     font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.zc-sql-affected { margin-top: 8px; color: #3f7d4e; font-size: 13px; }
.zc-sql-truncated { margin-top: 8px; color: #b45309; font-size: 12px; }
.zc-sql-summary { margin-top: 4px; padding: 6px 10px; border-radius: 4px; background: #eef3f8;
                  color: #3f5061; font-size: 12px; }
</style>
