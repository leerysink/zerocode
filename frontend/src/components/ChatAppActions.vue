<script setup>
// 聊天页底部应用操作条（票 001~004 / spec §5）。自持全部状态：反查 app、预览轮询、四个查看壳子的开合。
// 反查不到 app 就整组不渲染——GET /api/apps 已按 owner 过滤，一个判据同时覆盖
// 「未绑工作区 / 无应用 / 非本人应用」三种情形，前端不重复实现权限判定（ADR-0020）。
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import {
  CopyOutlined, DashboardOutlined, DatabaseOutlined, HistoryOutlined, MenuUnfoldOutlined,
  CloudUploadOutlined, ProfileOutlined, RedoOutlined, SelectOutlined, SyncOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { devReset, devRestart, devStart, listApps } from '../api'
import { useChatStore } from '../stores/chat'
import { useDevStatus } from '../composables/useDevStatus'
import { devActionLabel, findAppByWorkspace, isDevBusy } from '../utils/appLookup'
import { canPublish as stackPublishable } from '../utils/publishableStacks'
// 查看类内容一律复用应用列表那套现成组件，零改造、不抽公共层（ADR-0020）
import DeployLogPanel from './DeployLogPanel.vue'
import MetricsModal from './MetricsModal.vue'
import PublishHistory from './PublishHistory.vue'
import PublishModal from './PublishModal.vue'

const chatStore = useChatStore()
const apps = ref([])
const app = computed(() => findAppByWorkspace(apps.value, chatStore.currentWorkspaceId))
const appId = computed(() => app.value?.id ?? null)
// 轮询没回来之前先吃 listApps 带的那一份，入口不至于开局就没有状态读数
const polled = useDevStatus(appId, { onSettled: onDevSettled })
const devStatus = computed(() => polled.status.value ?? app.value?.status_dev ?? null)
// 202 受理与轮询捞回 starting 之间有最长 5s 空窗，这段时间靠本地 submitting 自锁
const submitting = ref(false)
const devBusy = computed(() => submitting.value || isDevBusy(devStatus.value))

// 地址只从接口字段来，绝不按 dev_port 自拼：部署基址是配置项，硬拼在容器化与远端访问下直接失效（ADR-0021）。
// dev_port 缺位时后端刻意回空串（app_service.py::_with_preview_url），此时开出去的是 about:blank，比不开更糟。
const previewUrl = computed(() => polled.url.value || app.value?.preview_url || '')
const canOpenPreview = computed(() => devStatus.value === 'running' && !!previewUrl.value)

// 四个查看壳子互斥：同时至多一个开着。用单一 active 源 + 可写 computed，
// 互斥由构造保证，不需要「开这个就关其余」的遍历；v-model:open 直绑布尔也是
// 本仓库既有的写法（AppsView 同款）。
// 旧壳挂着上一个应用的 id 继续显示是事故源——点「发布」会发到错的库上（ADR-0020 后果条）。
const active = ref(null)
const shellOf = (key) =>
  computed({ get: () => active.value === key, set: (v) => { active.value = v ? key : null } })
const logsOpen = shellOf('logs')
const metricsOpen = shellOf('metrics')
const historyOpen = shellOf('history')
const publishOpen = shellOf('publish')
// 语义仍是「打开某个壳子」，供菜单项与重建后的自动开日志调用
function openShell(key) {
  active.value = key
}

watch(() => chatStore.currentWorkspaceId, () => {
  // 切会话即清场，顺带解自锁：上一个会话的启动还在飞时切走，新会话不该继承那个转圈
  active.value = null
  submitting.value = false
})

// 栈闸门与列表页同源（utils/publishableStacks.js），不在聊天页另立一份名单
const canPublish = computed(() => stackPublishable(app.value))

// PublishModal 在 setup 期就读 props.app.id（useDeploy(props.app.id)），所以必须 v-if + :key
// 才挂、不能靠传 null 隐藏；seq 递增保证换应用或再开一次时拿到全新实例（与 AppsView.vue 的挂载写法同款）。
const pub = reactive({ seq: 0, app: null, mode: 'publish', version: null, autoStart: false })
function openPublish() {
  if (!canPublish.value) return
  Object.assign(pub, { app: app.value, mode: 'publish', version: null, autoStart: false, seq: pub.seq + 1 })
  openShell('publish')
}
// 记录页确认回滚 → 关掉记录、开进度弹层，由 autoStart 单点触发 rollbackApp
function startRollback(version) {
  Object.assign(pub, { app: app.value, mode: 'rollback', version, autoStart: true, seq: pub.seq + 1 })
  openShell('publish')
}

function openPreview() {
  if (!canOpenPreview.value) return
  // 命名窗口让同一应用重复点击复用标签而不是堆一排（对齐 openFolder.js 的 zerocode-files-<id>）
  const win = window.open(previewUrl.value, `zerocode-preview-${app.value.id}`)
  // 被弹窗拦截或 transient activation 过期时返回 null；静默失败等于按钮坏了
  if (!win) message.warning('新标签被拦截了，请允许本站弹出窗口后重试')
}

// 失败提示里挂一个可点的「查看日志」：排查启动失败时用户下一步几乎必然是看日志，
// 让他在 message 和菜单之间来回找是浪费。
function failWithLogs(text) {
  message.error({
    content: h('span', null, [
      text + ' ',
      h('a', { onClick: () => openShell('logs') }, '查看日志'),
    ]),
  })
}

function onDevSettled(kind) {
  submitting.value = false
  if (kind === 'running') message.success('预览已就绪')
  else failWithLogs(kind === 'timeout' ? '预览启动超时' : '预览启动失败')
}

async function loadApps() {
  apps.value = (await listApps()).data
}
onMounted(loadApps)

async function toggleDev() {
  const a = app.value
  if (!a || devBusy.value) return
  submitting.value = true
  // 解除上一轮的播报去重：应用本来就在 running 时点重启，绕一圈回到 running 也得再报一次
  polled.rearm()
  try {
    // restart 的后端语义就是 stop + start（engine.py::restart_dev），停着时点「启动预览」走 start 少一次无谓 down
    if (devStatus.value === 'running') await devRestart(a.id)
    else await devStart(a.id)
    message.info('预览启动中，稍候自动刷新')
  } catch (e) {
    submitting.value = false
    message.error(e?.response?.data?.detail || '操作失败')
  }
}

// 重建的是 dev 数据库不是容器栈：drop + provision + 从 sqlchange/ 全量 fold（app/api/apps.py::dev_reset）。
// 确认文案与形态逐字复用卡面（AppsView.vue::resetDev）；菜单里改叫「重建 dev 库」是因为它
// 紧邻「重启预览」，差一个字就会被读成重启的加强版（ADR-0020）。
function resetDev() {
  const a = app.value
  if (!a) return
  Modal.confirm({
    title: '重建 dev 数据库',
    content: `将删除并重建 ${a.slug}_dev（从 sqlchange 文件全量重放），用于消除 dev 漂移；不影响 ${a.slug}_prod。确认继续？`,
    okText: '重建',
    okType: 'danger',
    onOk: async () => {
      try {
        await devReset(a.id)
        // 结果只走 WS dev_reset_result，而本页按裁定不订阅 deploy WS——自动开日志是唯一可见通道
        openShell('logs')
      } catch (e) {
        message.error(e?.response?.data?.detail || '重建失败')
      }
    },
  })
}

async function copyPreviewUrl() {
  if (!previewUrl.value) return
  // clipboard 只在安全上下文（https 或 localhost）存在：用 IP + http 访问控制台时它是
  // undefined，直接 .writeText 抛 TypeError。失败必须说出来，不能点了没反应。
  try {
    if (!navigator.clipboard) throw new Error('clipboard unavailable outside a secure context')
    await navigator.clipboard.writeText(previewUrl.value)
    message.success('预览地址已复制')
  } catch {
    message.error('复制失败，请手动选择地址')
  }
}
</script>

<template>
  <div v-if="app" class="zc-app-actions">
    <a-button
      size="small"
      class="zc-act-dev"
      :disabled="devBusy"
      :loading="devBusy"
      @click="toggleDev"
    >
      <template #icon><sync-outlined /></template>
      {{ devBusy ? '启动中…' : devActionLabel(devStatus) }}
    </a-button>
    <a-tooltip :title="canOpenPreview ? '打开预览' : '预览未启动'">
      <a-button size="small" class="zc-act-open" :disabled="!canOpenPreview" @click="openPreview">
        <template #icon><select-outlined /></template>
      </a-button>
    </a-tooltip>
    <a-dropdown>
      <a-button size="small" class="zc-act-more">
        <template #icon><menu-unfold-outlined /></template>
      </a-button>
      <template #overlay>
        <div class="zc-app-menu">
          <div
            class="zc-app-menu-item zc-menu-copy"
            :class="{ 'is-disabled': !previewUrl }"
            @click="copyPreviewUrl"
          >
            <copy-outlined /> 复制预览地址
          </div>
          <div class="zc-app-menu-sep" />
          <a class="zc-app-menu-item zc-menu-db" :href="`/apps/${app.id}/db`" target="_blank" @click.stop>
            <database-outlined /> 数据库操作
          </a>
          <div class="zc-app-menu-item zc-menu-reset" @click="resetDev"><redo-outlined /> 重建 dev 库</div>
          <div class="zc-app-menu-sep" />
          <div
            class="zc-app-menu-item zc-menu-publish"
            :class="{ 'is-disabled': !canPublish }"
            @click="openPublish"
          >
            <cloud-upload-outlined /> 发布新版本
          </div>
          <div class="zc-app-menu-item zc-menu-history" @click="openShell('history')">
            <history-outlined /> 发布记录
          </div>
          <div class="zc-app-menu-item zc-menu-metrics" @click="openShell('metrics')">
            <dashboard-outlined /> 资源监控
          </div>
          <div class="zc-app-menu-item zc-menu-logs" @click="openShell('logs')">
            <profile-outlined /> 预览日志
          </div>
        </div>
      </template>
    </a-dropdown>

    <a-drawer
      v-model:open="logsOpen"
      :title="`日志 · ${app.name}`"
      width="62%"
      :body-style="{ padding: '12px 16px' }"
      destroy-on-close
    >
      <!-- active 驱动面板内部 WS 的连与断：抽屉关了不落回 false 就是一条僵尸连接 -->
      <deploy-log-panel
        v-if="logsOpen"
        :app-id="app.id"
        :app-name="app.name"
        :active="logsOpen"
      />
    </a-drawer>

    <a-modal
      v-model:open="metricsOpen"
      :width="920"
      :centered="true"
      :footer="null"
      destroy-on-close
    >
      <template #title>资源监控 · {{ app.name }}</template>
      <MetricsModal v-if="metricsOpen" :key="app.id" :app="app" />
    </a-modal>

    <a-drawer
      v-model:open="historyOpen"
      :title="`发布历史 · ${app.name}`"
      width="760px"
      destroy-on-close
    >
      <PublishHistory v-if="historyOpen" :app="app" @rollback="startRollback" />
    </a-drawer>

    <PublishModal
      v-if="publishOpen"
      :key="pub.seq"
      v-model:open="publishOpen"
      :app="pub.app"
      :mode="pub.mode"
      :rollback-version="pub.version"
      :auto-start="pub.autoStart"
      @done="loadApps"
    />
  </div>
</template>

<style scoped>
.zc-app-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.zc-app-menu {
  background: #fff;
  border: 1px solid var(--zc-border);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(47, 58, 71, 0.12);
  padding: 4px 0;
  min-width: 160px;
}
.zc-app-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
  color: inherit;
  text-decoration: none;
}
.zc-app-menu-item:hover {
  background: rgba(91, 122, 157, 0.1);
}
.zc-app-menu-sep {
  height: 1px;
  margin: 4px 0;
  background: var(--zc-border);
}
.zc-app-menu-item.is-disabled {
  color: var(--zc-text-sub);
  cursor: not-allowed;
}
.zc-app-menu-item.is-disabled:hover {
  background: none;
}
</style>
