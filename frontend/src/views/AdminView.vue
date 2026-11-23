<script setup>
// 账号治理台（仅管理员）：账号名册与封禁/解封。规格 2026-11-23 §3 C/F。
// 门禁双层：视图 + 后端 403（app/api/admin.py 的 require_admin）。
// 主语与设置页分明：设置页 = 对我自己配东西，这里 = 对别人的账号做事（ADR-0062 的分区纪律）。
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined, SaveOutlined, StopOutlined, UndoOutlined } from '@ant-design/icons-vue'
import * as api from '../api'
import { useAuthStore } from '../stores/auth'
import ZcPageHeader from '../components/zc/ZcPageHeader.vue'
import ZcEmptyState from '../components/zc/ZcEmptyState.vue'

const auth = useAuthStore()
const isFounder = computed(() => !!auth.user?.is_founder)
const myId = computed(() => auth.user?.id)

const accounts = ref([])
const loading = ref(false)
const banOpen = ref(false)
const banTarget = ref(null)
const banReason = ref('')
const banStopStacks = ref(false)
const working = ref(false)
const quotaOpen = ref(false)
const quotaTarget = ref(null)
const quotaForm = ref({ max_apps: null, max_running_stacks: null })
const quotaSaving = ref(false)

const columns = [
  { title: '昵称', dataIndex: 'nickname' },
  { title: '身份', dataIndex: 'is_founder', width: 90 },
  { title: '注册于', dataIndex: 'created_at', width: 170 },
  { title: '应用 已用/上限', key: 'apps', width: 140 },
  { title: '运行栈 已用/上限', key: 'stacks', width: 140 },
  { title: '状态', dataIndex: 'banned_at', width: 200 },
  { title: '操作', key: 'action', width: 190 },
]

// 上限为 null 有两种来源：管理员豁免（不该显示数字），与该档未设限。这里都渲染成「不限」，
// 但管理员那行额外标一句「不受闸」，免得读数骗人（ADR-0070）。
const fmtLimit = (row, value) => {
  if (row.is_founder) return '不限（管理员）'
  return value === null || value === undefined ? '不限' : value
}
const overLimit = (row, used, limit) => !row.is_founder
  && limit !== null && limit !== undefined && used > limit

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '')

const stats = computed(() => [
  { label: '账号', value: accounts.value.length },
  { label: '已封禁', value: accounts.value.filter((a) => a.banned_at).length },
])

async function load() {
  if (!isFounder.value) return
  loading.value = true
  try {
    accounts.value = (await api.adminAccounts()).data
  } catch (e) {
    message.error(e.response?.data?.detail || '账号列表加载失败')
  } finally {
    loading.value = false
  }
}

function openBan (row) {
  banTarget.value = row
  banReason.value = ''
  banStopStacks.value = false   // 每次回到「不停栈」：破坏性动作不做跨次记忆
  banOpen.value = true
}

async function confirmBan () {
  working.value = true
  try {
    const { data } = await api.adminBan(banTarget.value.id, {
      reason: banReason.value.trim(), stop_stacks: banStopStacks.value,
    })
    // 停栈是异步受理的：说「已受理 N 套」而不是「已停止 N 套」，别替用户脑补结果
    const extra = data.stacks_stopped ? `，并已受理停止 ${data.stacks_stopped} 套容器栈` : ''
    const busy = data.stacks_skipped
      ? `（${data.stacks_skipped} 个应用上有发布/迁移在跑，未强行插入）` : ''
    message.success(`已封禁 ${banTarget.value.nickname}，他的所有设备已下线${extra}${busy}`)
    banOpen.value = false
    await load()
  } catch (e) {
    message.error(e.response?.data?.detail || '封禁失败')
  } finally {
    working.value = false
  }
}

async function unban (row) {
  try {
    await api.adminUnban(row.id)
    message.success(`已解封 ${row.nickname}`)
    await load()
  } catch (e) {
    message.error(e.response?.data?.detail || '解封失败')
  }
}

function openQuota (row) {
  quotaTarget.value = row
  // null 原样带上：输入框留空就是「不限」，把它折成 0 或空串会改变语义
  quotaForm.value = { max_apps: row.apps_limit, max_running_stacks: row.stacks_limit }
  quotaOpen.value = true
}

async function saveQuota () {
  quotaSaving.value = true
  try {
    await api.adminPutQuota(quotaTarget.value.id, {
      max_apps: quotaForm.value.max_apps ?? null,
      max_running_stacks: quotaForm.value.max_running_stacks ?? null,
    })
    message.success(`已更新 ${quotaTarget.value.nickname} 的额度`)
    quotaOpen.value = false
    await load()
  } catch (e) {
    message.error(e.response?.data?.detail || '额度保存失败')
  } finally {
    quotaSaving.value = false
  }
}

// ---- 平台默认（ADR-0068）：owner NULL 打底行，普通账号的设置页对这些键是只读的 ----
const DEFAULT_KEYS = ['quota.max_apps', 'quota.max_running_stacks',
  'llm.base_url', 'llm.model', 'llm.api_key']
const defaults = ref({})
const defaultsRaw = ref({})
const defaultsSaving = ref(false)
const defaultsDirty = computed(() =>
  DEFAULT_KEYS.some((k) => JSON.stringify(defaults.value[k]) !== JSON.stringify(defaultsRaw.value[k])))

async function loadDefaults () {
  try {
    const data = (await api.adminConfig()).data
    defaults.value = Object.fromEntries(DEFAULT_KEYS.map((k) => [k, data[k] ?? null]))
    defaultsRaw.value = { ...defaults.value }
  } catch (e) {
    message.error(e.response?.data?.detail || '平台默认加载失败')
  }
}

async function saveDefaults () {
  defaultsSaving.value = true
  try {
    // 只发改动过的键：全量提交会把没看的字段一起按当前读数写回去
    const body = Object.fromEntries(DEFAULT_KEYS
      .filter((k) => JSON.stringify(defaults.value[k]) !== JSON.stringify(defaultsRaw.value[k]))
      .map((k) => [k, defaults.value[k]]))
    await api.adminPutConfig(body)
    message.success('平台默认已更新，未自配覆盖的账号即刻生效')
    await Promise.all([loadDefaults(), load()])
  } catch (e) {
    message.error(e.response?.data?.detail || '平台默认保存失败')
  } finally {
    defaultsSaving.value = false
  }
}

onMounted(() => { load(); loadDefaults() })
</script>

<template>
  <div class="zc-admin">
    <a-empty v-if="!isFounder" data-test="admin-denied" description="治理台仅管理员可见" />
    <template v-else>
      <zc-page-header data-test="admin-head" title="账号治理"
                      subtitle="账号封禁与解封、按人配额；邀请码与平台默认配置陆续接入本页"
                      :stats="stats">
        <template #actions>
          <a-button data-test="admin-refresh" :loading="loading" @click="load">
            <reload-outlined /> 刷新
          </a-button>
        </template>
      </zc-page-header>

      <div class="zc-panel" data-test="admin-defaults">
        <h3 class="zc-group-title">平台默认</h3>
        <p class="zc-quota-note">
          这些是 owner NULL 的打底行：没有任何覆盖时全平台共用。留空 = 该档不限。
          <b>填在这里的模型密钥由全平台共用</b>——所有未自配密钥的账号都会拿它去请求。
        </p>
        <div class="zc-defaults-grid">
          <label>默认创建上限<a-input-number v-model:value="defaults['quota.max_apps']" :min="0"
                 :precision="0" allow-clear placeholder="不限" data-test="def-max-apps" /></label>
          <label>默认启动上限<a-input-number v-model:value="defaults['quota.max_running_stacks']"
                 :min="0" :precision="0" allow-clear placeholder="不限"
                 data-test="def-max-stacks" /></label>
          <label>Base URL<a-input v-model:value="defaults['llm.base_url']"
                 placeholder="https://api.example.com/v1" data-test="def-base-url" /></label>
          <label>模型<a-input v-model:value="defaults['llm.model']" placeholder="deepseek-chat"
                 data-test="def-model" /></label>
          <label>API Key（留 sk-*** 表示不覆盖）
            <a-input-password v-model:value="defaults['llm.api_key']" placeholder="sk-***"
                              data-test="def-api-key" /></label>
        </div>
        <a-button type="primary" :loading="defaultsSaving" :disabled="!defaultsDirty"
                  data-test="def-save" @click="saveDefaults">
          <save-outlined /> 保存平台默认
        </a-button>
      </div>

      <div class="zc-panel">
        <a-table data-test="admin-accounts" class="zc-accounts" :data-source="accounts"
                 :loading="loading" :columns="columns" row-key="id" :pagination="false" size="small">
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'is_founder'">
              <a-tag :color="record.is_founder ? 'blue' : 'default'" data-test="acct-role">
                {{ record.is_founder ? '管理员' : '普通账号' }}
              </a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'created_at'">
              {{ fmtTime(record.created_at) }}
            </template>
            <template v-else-if="column.key === 'apps'">
              <span :class="{ 'zc-over': overLimit(record, record.apps_used, record.apps_limit) }"
                    data-test="acct-apps">
                {{ record.apps_used }} / {{ fmtLimit(record, record.apps_limit) }}
              </span>
            </template>
            <template v-else-if="column.key === 'stacks'">
              <span :class="{ 'zc-over': overLimit(record, record.stacks_running, record.stacks_limit) }"
                    data-test="acct-stacks">
                {{ record.stacks_running }} / {{ fmtLimit(record, record.stacks_limit) }}
              </span>
            </template>
            <template v-else-if="column.dataIndex === 'banned_at'">
              <span v-if="record.banned_at" class="zc-banned" data-test="acct-banned">
                已封禁 · {{ fmtTime(record.banned_at) }}
                <span v-if="record.banned_reason" class="zc-banned-reason">（{{ record.banned_reason }}）</span>
              </span>
              <span v-else class="zc-ok" data-test="acct-active">正常</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button type="link" size="small" :data-test="`acct-quota-${record.nickname}`"
                        @click="openQuota(record)">额度</a-button>
              <a-button v-if="record.id === myId" type="link" size="small" disabled
                        data-test="acct-self">本人</a-button>
              <a-button v-else-if="record.banned_at" type="link" size="small"
                        data-test="acct-unban" @click="unban(record)">
                <undo-outlined /> 解封
              </a-button>
              <a-button v-else type="link" danger size="small"
                        :data-test="`acct-ban-${record.nickname}`" @click="openBan(record)">
                <stop-outlined /> 封禁
              </a-button>
            </template>
          </template>
        </a-table>
        <zc-empty-state v-if="!loading && !accounts.length" description="还没有账号" />
      </div>

      <a-modal v-model:open="banOpen" :confirm-loading="working" ok-text="确认封禁"
               :ok-button-props="{ danger: true }" data-test="ban-modal"
               title="封禁账号" @ok="confirmBan">
        <p>
          封禁 <b>{{ banTarget?.nickname }}</b> 后，他的<b>所有设备立即下线</b>且无法再登录，
          正在运行的容器栈默认不受影响。应用、数据与配置都保留，解封后原样可用。
        </p>
        <a-textarea v-model:value="banReason" :maxlength="200" show-count
                    placeholder="封禁原因（可选，会展示在账号列表里）"
                    :auto-size="{ minRows: 2, maxRows: 4 }" data-test="ban-reason" />
        <a-checkbox v-model:checked="banStopStacks" data-test="ban-stop-stacks"
                    class="zc-ban-stop">同时停止他正在跑的容器栈</a-checkbox>
        <p v-if="banStopStacks" class="zc-quota-note zc-danger-hint">
          这会<b>中断</b>他正在进行的预览、发布与迁移，且解封不会自动把栈起回来。
        </p>
      </a-modal>

      <a-modal v-model:open="quotaOpen" :confirm-loading="quotaSaving" ok-text="保存"
               data-test="quota-modal" :title="`设置额度 · ${quotaTarget?.nickname || ''}`"
               @ok="saveQuota">
        <p class="zc-quota-note">
          留空 = <b>不限</b>（回落到平台默认）；填 <b>0</b> = 一个都不许。
          下调额度<b>不会</b>动他已有的应用与正在跑的栈，只是下一次创建/启动前得自己降下来。
          管理员自己不受这两把闸约束。
        </p>
        <a-form layout="vertical">
          <a-form-item label="可创建应用数">
            <a-input-number v-model:value="quotaForm.max_apps" :min="0" :precision="0"
                            allow-clear placeholder="不限" style="width: 100%"
                            data-test="quota-max-apps" />
          </a-form-item>
          <a-form-item label="可同时运行的容器栈数（dev 与 prod 各算一个）">
            <a-input-number v-model:value="quotaForm.max_running_stacks" :min="0" :precision="0"
                            allow-clear placeholder="不限" style="width: 100%"
                            data-test="quota-max-stacks" />
          </a-form-item>
        </a-form>
      </a-modal>
    </template>
  </div>
</template>

<style scoped>
.zc-admin {
  display: flex;
  flex-direction: column;
  gap: var(--zc-space-4);
  padding: var(--zc-space-5);
}
.zc-accounts :deep(.ant-table-cell) { vertical-align: middle; }
.zc-banned { color: var(--zc-danger); }
.zc-banned-reason { color: var(--zc-text-sub); }
.zc-ok { color: var(--zc-text-sub); }
/* 超额是「已用量 > 上限」的既成事实（下调造成的），只标红提示，不禁用任何既有操作 */
.zc-over { color: var(--zc-danger); font-weight: 500; }
.zc-quota-note { color: var(--zc-text-sub); font-size: 12px; line-height: 1.6; }
.zc-ban-stop { display: block; margin-top: var(--zc-space-3); }
.zc-danger-hint { color: var(--zc-danger); margin-top: 4px; }
.zc-group-title { margin: 0 0 var(--zc-space-3); font-size: 14px; }
.zc-defaults-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--zc-space-3);
  margin-bottom: var(--zc-space-4);
}
.zc-defaults-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.zc-defaults-grid .ant-input-number { width: 100%; }
</style>
