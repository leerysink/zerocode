<script setup>
// 查询控制台（Ticket 002 示踪弹：一个框、多条语句、多个结果块）。
//
// 本票打通"拆段 → 批量端点 → 逐条结果块渲染"的主流程；
// 语句级失败与 skipped（003）、假分号状态机（004）、多页签与本地保存（005）、
// 格式化与选区优先执行（006）、prod 确认词（007）都在后续票里长出来。
import { ref, watch } from 'vue'
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

// 换环境即丢上一批结果：不把 dev 的结果糊在 prod 页面上（脚本本身按环境各自保存在 005）。
watch(() => props.env, () => { results.value = [] })

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
  try {
    results.value = (await dbSqlBatch(props.appId, props.env, list)).data.results
  } catch (e) {
    message.error(e?.response?.data?.detail || '执行失败')
  } finally {
    busy.value = false
  }
}

defineExpose({ text, results, busy, run, submit })
</script>

<template>
  <div class="zc-sql">
    <SqlEditor v-model="text" :tables="tables" class="zc-sql-input" />
    <div class="zc-sql-tools">
      <a-button class="zc-sql-run" type="primary" :disabled="busy" @click="run">执行</a-button>
      <span class="zc-sql-hint">多条以分号分隔，按序逐条执行</span>
      <span class="zc-sql-env">当前环境：{{ env }}</span>
    </div>
    <div v-for="(r, i) in results" :key="i" class="zc-sql-block">
      <div class="zc-sql-block-head">
        第 {{ (r.index ?? i) + 1 }}/{{ results.length }} 条
        <span class="zc-sql-block-op">{{ r.op }}</span>
        <span class="zc-sql-block-cost">{{ r.duration_ms }}ms</span>
      </div>
      <pre class="zc-sql-block-text">{{ r.sql }}</pre>
      <template v-if="r.kind === 'rows'">
        <a-table size="small" :dataSource="r.rows"
                 :columns="r.columns.map(c => ({ title: c, dataIndex: c }))" :pagination="false" />
        <div v-if="r.truncated" class="zc-sql-truncated">
          结果过大，仅显示前 {{ r.rows.length }} 行；请用 LIMIT / WHERE 收窄。
        </div>
      </template>
      <div v-else-if="r.kind === 'affected'" class="zc-sql-affected">影响 {{ r.affected }} 行</div>
    </div>
  </div>
</template>

<style scoped>
.zc-sql-tools { display: flex; align-items: center; gap: 12px; margin: 8px 0 12px; }
.zc-sql-hint, .zc-sql-env { color: #6b7280; font-size: 12px; }
.zc-sql-block { border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
.zc-sql-block-head { display: flex; gap: 8px; align-items: center; font-size: 12px; color: #6b7280; }
.zc-sql-block-op { padding: 0 6px; border-radius: 4px; background: #eef3f8; color: #2f6fa7; }
.zc-sql-block-text { margin: 6px 0 8px; font-family: ui-monospace, Consolas, monospace;
                     font-size: 12px; color: #3f5061; white-space: pre-wrap; }
.zc-sql-affected { margin-top: 8px; color: #3f7d4e; font-size: 13px; }
.zc-sql-truncated { margin-top: 8px; color: #b45309; font-size: 12px; }
</style>
