<script setup>
// 发布历史（M2 / T10，规格 04 §4.4 + FR4-08）：记账表 + 回滚入口 + 记录详情/删除。
// 回滚不在这里执行——确认链走 emit('rollback', version)，由父级打开 PublishModal
// （autoStart 模式）单点调 rollbackApp：进度通道与发布共用同一条 WS 订阅（T7 注：
// rollback 复用 publish_step 事件协议），错误收口也只在弹层一处呈现。
import { onMounted, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import { appRecords, deleteRecord } from '../api'

const props = defineProps({ app: { type: Object, required: true } })
const emit = defineEmits(['rollback'])

const STEP_LABELS = {
  preflight: '发布前检查', build: '镜像构建', provision: '数据库供给', migrate: '数据库迁移',
  deploy: '正式栈启动', health: '联合体检', record: '发布记账',
}
const STATUS_TEXT = { success: '成功', failed: '失败', running: '进行中', interrupted: '已中断' }
// 终审 Minor-1：中断收口的记录是 failed+step=interrupted（mark_interrupted 写库形状），
// 不是独立 status——普通失败与平台重启中断在历史表里要能分辨（README 对用户承诺了这一点）。
const statusText = (r) => (r.status === 'failed' && r.step === 'interrupted' ? '已中断' : STATUS_TEXT[r.status] || r.status)
const ROLLBACK_WARN = '回滚只恢复程序版本，数据库结构与数据保持当前状态；'
  + '若新版本改过表结构，旧程序可能读写异常，请联系 AI 生成兼容处理。'

const columns = [
  { title: '时间', key: 'time', dataIndex: 'started_at', width: 150 },
  { title: '类型', key: 'kind', width: 56 },
  { title: '版本', key: 'version', dataIndex: 'version' },
  { title: '状态', key: 'status', width: 140 },
  { title: '耗时', key: 'duration', width: 70 },
  { title: '操作', key: 'actions', width: 210 },
]
const records = ref([])
const loading = ref(true)
const viewing = ref(null)

async function load() {
  loading.value = true
  try {
    records.value = (await appRecords(props.app.id)).data
  } catch (e) {
    message.error(e?.response?.data?.detail || '发布记录加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(load)

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function duration(r) {
  const ms = r.detail?.durationMs
  if (ms != null) return `${Math.max(1, Math.round(ms / 1000))}s`
  if (r.finished_at && r.started_at) {
    return `${Math.max(1, Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000))}s`
  }
  return '—'
}
const canRollback = (r) => r.kind === 'publish' && r.version !== props.app.current_version
function askRollback(r) {
  Modal.confirm({
    title: `回滚到版本 ${r.version}？`, content: ROLLBACK_WARN,
    okText: '确认回滚', cancelText: '取消',
    onOk: () => emit('rollback', r.version),
  })
}
async function remove(r) {
  try {
    await deleteRecord(props.app.id, r.id)
    message.success('记录已删除（发布镜像已尽力清理）')
    if (viewing.value?.id === r.id) viewing.value = null
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}
</script>

<template>
  <a-spin v-if="loading" />
  <template v-else>
    <a-empty v-if="!records.length" description="还没有发布记录——点上方『发布』上线你的应用" />
    <a-table v-else :columns="columns" :data-source="records" :pagination="false"
             row-key="id" size="middle">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'time'">{{ fmtTime(record.started_at) }}</template>
        <template v-else-if="column.key === 'kind'">{{ record.kind === 'publish' ? '发布' : '回滚' }}</template>
        <template v-else-if="column.key === 'status'">
          <span class="zc-rdot" :class="record.status === 'failed' && record.step === 'interrupted'
                ? 'dot-interrupted' : 'dot-' + record.status" />
          {{ statusText(record) }}
          <template v-if="record.status === 'running' && record.step">
            · {{ STEP_LABELS[record.step] || record.step }}
          </template>
        </template>
        <template v-else-if="column.key === 'duration'">{{ duration(record) }}</template>
        <template v-else-if="column.key === 'actions'">
          <a-button size="middle" class="zc-btn-log" @click="viewing = record">查看日志</a-button>
          <a-button size="middle" class="zc-btn-rollback" :disabled="!canRollback(record)"
                    :title="canRollback(record) ? '' : '仅历史发布版本可回滚'"
                    @click="askRollback(record)">回滚到此版本</a-button>
          <a-popconfirm title="删除该记录？关联发布镜像会尽力清理，数据库数据不受影响。"
                        @confirm="remove(record)">
            <a-button size="middle" class="zc-btn-del">删除</a-button>
          </a-popconfirm>
        </template>
      </template>
    </a-table>

    <div v-if="viewing" class="zc-rec-detail">
      <p><b>版本 {{ viewing.version }}</b> · {{ fmtTime(viewing.started_at) }}
        <span class="zc-rec-close" @click="viewing = null">收起 ✕</span></p>
      <p>SQL 迁移：{{ (viewing.detail?.sqlApplied || []).join('、') || '无' }}</p>
      <pre>{{ viewing.error || '（无错误）' }}</pre>
    </div>
  </template>
</template>

<style scoped>
.zc-rdot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
.zc-rdot.dot-success { background: #5b8a72; }
.zc-rdot.dot-failed, .zc-rdot.dot-interrupted { background: #b96a6a; }
.zc-rdot.dot-running { background: #5b7a9d; }
.zc-rec-detail { margin-top: 12px; border: 1px solid var(--zc-border); border-radius: 6px;
                 padding: 10px 14px; font-size: 12px; }
.zc-rec-detail p { margin: 0 0 6px; }
.zc-rec-detail pre { background: #f7f8fa; border-radius: 4px; padding: 8px 10px; margin: 0;
                     font-size: 11px; white-space: pre-wrap; word-break: break-all;
                     max-height: 200px; overflow-y: auto; }
.zc-rec-close { float: right; cursor: pointer; color: var(--zc-text-sub); }
</style>
