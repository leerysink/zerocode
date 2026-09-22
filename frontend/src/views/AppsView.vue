<script setup>
// 应用页（M1 卡片/向导/预览抽屉 + M2 发布接线）：发布/回滚进度弹层与发布历史抽屉都只在
// 打开时挂载（v-if）——WS 订阅单实例纪律由 PublishModal 自身的 connect/close 保证；
// 卡片 prod 状态靠既有 5s 轮询刷新，正式地址 hover 才懒加载 appUrls（不轰炸后端）。
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  AppstoreOutlined, CloudUploadOutlined, CopyOutlined, EyeOutlined, HistoryOutlined,
  MenuOutlined, PlayCircleOutlined, PoweroffOutlined, ReloadOutlined, RocketOutlined,
  ShopOutlined,
} from '@ant-design/icons-vue'
import {
  appUrls, createApp, deleteApp, devReset, devRestart, devStart, devStatus, devStop, listApps,
  openFolder, prodStart, prodStop, releaseToStore, renameApp, validateAppSlug,
} from '../api'
import PublishHistory from '../components/PublishHistory.vue'
import PublishModal from '../components/PublishModal.vue'
import { SMALL_STACKS, slugFromName, validateForm } from '../utils/appForm'
import DeployLogPanel from '../components/DeployLogPanel.vue'
import BackButton from '../components/BackButton.vue'
import WorkspaceExplorer from '../components/WorkspaceExplorer.vue'
import { resolveOpenFolderMode } from '../utils/openFolder'
import { useConfigStore } from '../stores/config'

const apps = ref([])
const loading = ref(true)
const showWizard = ref(false)
const current = ref(0)
const preview = reactive({ open: false, app: null, url: '', status: '', nonce: 0 })
const logs = reactive({ open: false, app: null })
const pub = reactive({ open: false, app: null, mode: 'publish', version: null, autoStart: false, seq: 0 })
const hist = reactive({ open: false, app: null })
const rel = reactive({ open: false, app: null, title: '', summary: '' })
const urlsCache = reactive({})
const rename = reactive({ open: false, app: null, name: '' })
const del = reactive({ open: false, app: null })
// 内置文件管理器（远端访问 / 直开失败时的兜底），记哪个应用在用它
const explorer = reactive({ open: false, app: null })
const openingFolder = ref(false)
const cfg = useConfigStore()
const form = reactive({ name: '', slug: '', slugTouched: false, scale: 'small', stack: 'vue3-fastapi' })
let timer = null

const scaleCards = [
  { value: 'small', title: '小型', desc: '表单/台账/查询类，选一种轻量后端' },
  { value: 'large', title: '大型', desc: '多角色/流程类业务系统，Vue3 + Spring Boot 3' },
]

async function load() {
  loading.value = true
  try {
    apps.value = (await listApps()).data
  } catch (e) {
    message.error(e?.response?.data?.detail || '应用列表加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(() => { load(); timer = setInterval(load, 5000) })
onUnmounted(() => clearInterval(timer))

function onName() {
  if (!form.slugTouched) form.slug = slugFromName(form.name)
}
async function onSlug() {
  form.slugTouched = true
  if (!form.slug) return
  try {
    const r = (await validateAppSlug(form.slug)).data
    if (!r.ok) message.warning(`该 slug 不可用，建议：${r.suggest}`)
  } catch (e) {
    message.error(e?.response?.data?.detail || 'slug 校验失败')
  }
}
function nextFrom1() {
  if (!form.name.trim()) return message.warning('请填写应用名称')
  if (!/^[a-z][a-z0-9]{1,39}$/.test(form.slug)) return message.warning('slug 非法')
  current.value = 1
}
function nextFrom2() {
  if (form.scale === 'large') form.stack = 'vue3-springboot'
  current.value = 2
}
async function submit() {
  const v = validateForm(form)
  if (!v.ok) return message.warning(v.msg)
  try {
    await createApp({ name: form.name.trim(), slug: form.slug, scale: form.scale, stack: form.stack })
    message.success('应用已创建')
    showWizard.value = false
    Object.assign(form, { name: '', slug: '', slugTouched: false, scale: 'small', stack: 'vue3-fastapi' })
    current.value = 0
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '创建失败')
  }
}

const statusColor = (s) => ({
  running: '#5b8a72', stopped: '#9aa0a6', starting: '#5b7a9d',
  publishing: '#5b7a9d', error: '#b96a6a', unhealthy: '#c08a3e',
}[s || 'stopped'])
const statusText = (s) => ({
  running: '运行中', stopped: '已停止', starting: '启动中…',
  publishing: '发布中…', error: '出错了', unhealthy: '体检未通过',
}[s || 'stopped'])

async function openPreview(a) {
  preview.open = true
  preview.app = a
  await refreshPreview(a)
}
async function refreshPreview(a) {
  try {
    const st = (await devStatus(a.id)).data
    preview.status = st.status_dev
    preview.url = st.url
    preview.nonce++
  } catch (e) {
    message.error(e?.response?.data?.detail || '预览状态获取失败')
  }
}
async function toggleDev(a) {
  try {
    if (a.status_dev === 'running') {
      await devStop(a.id)
      message.success('已停止预览')
    } else {
      await devStart(a.id)
      message.info('预览启动中，稍候自动刷新')
    }
    setTimeout(load, 1200)
  } catch (e) {
    message.error(e?.response?.data?.detail || '操作失败')
  }
}
async function restartDev(a) {
  try {
    await devRestart(a.id)
    message.info('重启中…')
    setTimeout(load, 1500)
  } catch (e) {
    message.error(e?.response?.data?.detail || '重启失败')
  }
}
async function resetDev(a) {
  // 破坏性：drop 并重建 <slug>_dev，从 sqlchange 文件全量 fold；prod 不受影响。
  Modal.confirm({
    title: '重建 dev 数据库',
    content: `将删除并重建 ${a.slug}_dev（从 sqlchange 文件全量重放），用于消除 dev 漂移；不影响 ${a.slug}_prod。确认继续？`,
    okText: '重建',
    okType: 'danger',
    onOk: async () => {
      try {
        await devReset(a.id)
        message.info('dev 重建已受理，进度见查看日志')
      } catch (e) {
        message.error(e?.response?.data?.detail || '重建失败')
      }
    },
  })
}
async function copyUrl(url) {
  await navigator.clipboard.writeText(url)
  message.success('已复制')
}
// 查看日志：独立抽屉，栈没起也能打开（上一次启动失败在哪一步，只有这里看得到）
function openLogs(a) {
  logs.app = a
  logs.open = true
}

// ---- 卡片 ☰ 菜单：重命名 / 打开工程目录 / 复制预览地址 / 查看日志 / 删除 ----
const devUrl = (a) => `http://localhost:${a.dev_port}`

function openRename(a) {
  rename.app = a
  rename.name = a.name
  rename.open = true
}
const renameOk = computed(() => !!rename.name.trim())
async function doRename() {
  if (!rename.app || !renameOk.value) return
  try {
    await renameApp(rename.app.id, rename.name.trim())
    message.success('已重命名')
    rename.open = false
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '重命名失败')
  }
}

// 打开工程目录：与右栏文件面板同一套判断——本机直开系统文件管理器，
// 远端访问（服务端开窗用户看不见）或直开失败就当场降级到内置文件管理器。
async function openAppFolder(a) {
  if (openingFolder.value) return
  openingFolder.value = true
  try {
    await cfg.ensureLoaded()
    if (resolveOpenFolderMode(cfg.form.openFolderMode, window.location.hostname) === 'web') {
      explorer.app = a
      explorer.open = true
      return
    }
    await openFolder(a.workspace_id)
    message.success('已在系统文件管理器中打开工程目录')
  } catch (e) {
    explorer.app = a
    explorer.open = true
    message.info('无法直接打开文件夹，已改用内置文件管理器')
  } finally {
    openingFolder.value = false
  }
}

async function copyPreviewUrl(a) {
  try {
    await navigator.clipboard.writeText(devUrl(a))
    message.success('预览地址已复制')
  } catch (e) {
    message.error('复制失败，请手动选择地址')
  }
}

function openDelete(a) {
  del.app = a
  del.open = true
}
// 卡片上的删除走一次确认：只删平台记录与会话，工程目录与数据库都还在，
// 确认文案必须把「删了什么、留了什么」说清，而不是加一道打字闸门。
async function doDelete() {
  if (!del.app) return
  const a = del.app
  try {
    const { data } = await deleteApp(a.id)
    const kept = data?.kept
    message.success(kept?.root_path ? `应用已删除，工程目录保留在 ${kept.root_path}` : '应用已删除')
    del.open = false
    closeOverlays(a.id)
    await load()
  } catch (e) {
    message.error(e?.response?.data?.detail || '删除失败')
  }
}

// 应用没了还留着它的抽屉：预览/日志面板会继续拿死 id 轮询状态，只会刷出一串错误
function closeOverlays(appId) {
  if (preview.open && preview.app?.id === appId) preview.open = false
  if (logs.open && logs.app?.id === appId) {
    logs.open = false
    logs.app = null
  }
  if (explorer.open && explorer.app?.id === appId) explorer.open = false
}

// ---------- M2 发布接线 ----------
// 栈闸门：M2 引擎只支持 vue3-fastapi（后端未拦、UI 拦——static/其他栈按钮置灰带说明）
const canPublish = (a) => a.stack === 'vue3-fastapi'
function openPublish(a) {
  Object.assign(pub, { app: a, mode: 'publish', version: null, autoStart: false,
    seq: pub.seq + 1, open: true })
}
function openHistory(a) {
  hist.app = a
  hist.open = true
}
// 历史页确认回滚 → 关掉抽屉、开进度弹层（autoStart 单点触发 rollbackApp）
function startRollback(version) {
  const app = hist.app
  hist.open = false
  Object.assign(pub, { app, mode: 'rollback', version, autoStart: true,
    seq: pub.seq + 1, open: true })
}
async function toggleProd(a) {
  try {
    if (a.status_prod === 'running') {
      await prodStop(a.id)
      message.success('正式环境已停止')
    } else {
      await prodStart(a.id)
      message.info('正式环境启动中，稍候自动刷新')
    }
    setTimeout(load, 1200)
  } catch (e) {
    message.error(e?.response?.data?.detail || '操作失败')
  }
}
async function ensureUrls(a) {
  if (urlsCache[a.id]) return
  try {
    urlsCache[a.id] = (await appUrls(a.id)).data
  } catch { /* hover 懒加载失败静默，卡片仍有端口兜底 */ }
}
async function copyProdUrl(a) {
  await ensureUrls(a)
  const u = urlsCache[a.id]
  await copyUrl(u?.lan || u?.localhost || `http://localhost:${a.prod_port}`)
}

// ---------- M3-a 商店：发布到商店（只认真实在服的 prod 版本，闸门在后端） ----------
const canReleaseToStore = (a) => a.stack === 'vue3-fastapi' && !!a.current_version && !a.store
function openRelease(a) {
  Object.assign(rel, { app: a, title: a.name, summary: '', open: true })
}
async function doRelease() {
  if (!rel.app) return
  try {
    await releaseToStore(rel.app.id, { title: rel.title.trim() || rel.app.name, summary: rel.summary })
    message.success('已上架商店，可在侧栏「商店」页看到')
    rel.open = false
    await load()
  } catch (e) {
    const d = e?.response?.data?.detail
    message.error(typeof d === 'string' ? d : '上架失败')
  }
}
</script>

<template>
  <div class="zc-apps">
    <div class="zc-head">
      <div class="zc-head-left">
        <back-button />
        <h2><appstore-outlined /> 我的应用</h2>
      </div>
      <a-space>
        <a-button @click="load"><reload-outlined /> 刷新</a-button>
        <a-button type="primary" @click="showWizard = true"><rocket-outlined /> 新建应用</a-button>
      </a-space>
    </div>

    <a-spin v-if="loading" class="zc-spin" />
    <a-empty v-else-if="!apps.length" description="还没有应用——点「新建应用」，用一句话描述你要的软件"
             style="margin-top: 80px" />
    <div v-else class="zc-grid">
      <a-card v-for="a in apps" :key="a.id" class="zc-card">
        <template #title>
          <span class="zc-dot" :style="{ background: statusColor(a.status_dev) }" />
          <span class="zc-card-name">{{ a.name }}</span>
          <a-tag v-if="a.store" class="zc-store-tag">来自商店</a-tag>
          <a-tag class="zc-card-tag">{{ a.scale === 'large' ? '大型' : '小型' }} · {{ a.stack }}</a-tag>
        </template>
        <template #extra>
          <a-dropdown :trigger="['click']" placement="bottomRight">
            <menu-outlined class="zc-card-menu-btn" title="更多操作" @click.stop />
            <template #overlay>
              <div class="zc-app-menu">
                <div class="zc-app-menu-item zc-menu-rename" @click.stop="openRename(a)">重命名</div>
                <div class="zc-app-menu-item zc-menu-folder" @click.stop="openAppFolder(a)">打开工程目录</div>
                <div class="zc-app-menu-item zc-menu-copy" @click.stop="copyPreviewUrl(a)">复制预览地址</div>
                <div class="zc-app-menu-item zc-menu-logs" @click.stop="openLogs(a)">查看日志</div>
                <div class="zc-app-menu-item zc-app-menu-danger zc-menu-del" @click.stop="openDelete(a)">
                  删除应用
                </div>
              </div>
            </template>
          </a-dropdown>
        </template>
        <p class="zc-line">
          预览：{{ statusText(a.status_dev) }} · 端口 {{ a.dev_port }}
          <a v-if="a.status_dev === 'running'" :href="`http://localhost:${a.dev_port}`"
             target="_blank">打开</a>
        </p>
        <p class="zc-line sub" @mouseenter="ensureUrls(a)">
          正式：<span class="zc-dot" :style="{ background: statusColor(a.status_prod) }" />{{ statusText(a.status_prod) }}
          <template v-if="a.current_version"> · {{ a.current_version }}</template>
          <a v-if="a.status_prod === 'running'" href="#" @click.prevent="copyProdUrl(a)">
            <copy-outlined /> 正式地址
          </a>
        </p>
        <a-space wrap>
          <a-button size="middle" @click="openPreview(a)"><eye-outlined /> 预览</a-button>
          <a-button size="middle" @click="toggleDev(a)">
            <poweroff-outlined v-if="a.status_dev === 'running'" />
            <play-circle-outlined v-else />
            {{ a.status_dev === 'running' ? '停止' : '启动预览' }}
          </a-button>
          <a-button size="middle" @click="restartDev(a)">重启</a-button>
          <a-button size="middle" @click="resetDev(a)">重建 dev</a-button>
          <a-button type="primary" ghost :disabled="!canPublish(a)"
                    :title="canPublish(a) ? '' : '该栈发布支持即将上线'"
                    :loading="a.status_prod === 'publishing'" @click="openPublish(a)">
            <cloud-upload-outlined /> 发布
          </a-button>
          <a-button size="middle" @click="toggleProd(a)">
            <poweroff-outlined v-if="a.status_prod === 'running'" />
            <play-circle-outlined v-else />
            {{ a.status_prod === 'running' ? '停止正式' : '启动正式' }}
          </a-button>
          <a-button size="middle" @click="openHistory(a)"><history-outlined /> 发布历史</a-button>
          <a-button v-if="canReleaseToStore(a)" size="middle" @click="openRelease(a)">
            <shop-outlined /> 发布到商店
          </a-button>
        </a-space>
      </a-card>
    </div>

    <a-modal v-model:open="showWizard" title="新建应用" :footer="null" width="560px" destroy-on-close>
      <a-steps :current="current" size="small" style="margin-bottom: 20px"
               :items="[{ title: '起名字' }, { title: '选规模' }, { title: '选技术栈' }]" />
      <div v-if="current === 0">
        <a-form layout="horizontal" :label-col="{ style: { width: '120px', textAlign: 'right' } }">
          <a-form-item label="应用名称" required>
            <a-input v-model:value="form.name" placeholder="如：来料登记" @input="onName" />
          </a-form-item>
          <a-form-item label="应用标识" required help="小写字母开头，仅小写字母/数字，2-40 位">
            <a-input v-model:value="form.slug" placeholder="如：lailiao" @blur="onSlug" />
          </a-form-item>
        </a-form>
        <div class="zc-wizard-foot">
          <a-button type="primary" @click="nextFrom1">下一步</a-button>
        </div>
      </div>
      <div v-else-if="current === 1">
        <div v-for="s in scaleCards" :key="s.value" class="zc-pick" :class="{ on: form.scale === s.value }"
             @click="form.scale = s.value">
          <b>{{ s.title }}</b><span>{{ s.desc }}</span>
        </div>
        <div class="zc-wizard-foot">
          <a-button style="margin-right: 8px" @click="current = 0">上一步</a-button>
          <a-button type="primary" @click="nextFrom2">下一步</a-button>
        </div>
      </div>
      <div v-else>
        <a-form layout="horizontal" :label-col="{ style: { width: '120px', textAlign: 'right' } }">
          <a-form-item label="技术栈" required>
            <a-select v-model:value="form.stack" :options="form.scale === 'large'
              ? [{ value: 'vue3-springboot', label: 'Vue3 + Spring Boot 3（默认）', disabled: false }]
              : SMALL_STACKS" />
          </a-form-item>
        </a-form>
        <p class="zc-tip">数据库将自动使用 PostgreSQL（dev / prod 两套独立库）。</p>
        <div class="zc-wizard-foot">
          <a-button style="margin-right: 8px" @click="current = 1">上一步</a-button>
          <a-button type="primary" @click="submit">创建应用</a-button>
        </div>
      </div>
    </a-modal>

    <a-drawer v-model:open="preview.open" :title="`预览 · ${preview.app?.name || ''}`" width="80%">
      <a-space style="margin-bottom: 12px">
        <a-tag :color="preview.status === 'running' ? 'green' : 'default'">{{ statusText(preview.status) }}</a-tag>
        <a-button size="middle" @click="preview.app && refreshPreview(preview.app)"><reload-outlined /> 刷新</a-button>
        <a-button size="middle" v-if="preview.url" @click="copyUrl(preview.url)">
          <copy-outlined /> 复制地址
        </a-button>
        <a-button size="middle" v-if="preview.url" target="_blank" :href="preview.url">新窗口打开</a-button>
      </a-space>
      <iframe v-if="preview.status === 'running' && preview.url"
              :key="preview.nonce" :src="preview.url"
              style="width: 100%; height: calc(100vh - 160px); border: 1px solid #e5e7eb; border-radius: 4px" />
      <a-empty v-else description="预览未启动——点卡片上的「启动预览」，或稍候刷新" />
    </a-drawer>

    <a-drawer v-model:open="logs.open" :title="`日志 · ${logs.app?.name || ''}`" width="62%"
              :body-style="{ padding: '12px 16px' }" destroy-on-close>
      <deploy-log-panel v-if="logs.app" :app-id="logs.app.id" :app-name="logs.app.name"
                        :active="logs.open" />
    </a-drawer>

    <PublishModal v-if="pub.open" :key="pub.seq" v-model:open="pub.open" :app="pub.app"
                  :mode="pub.mode" :rollback-version="pub.version" :auto-start="pub.autoStart"
                  @done="load" />

    <a-drawer v-model:open="hist.open" :title="`发布历史 · ${hist.app?.name || ''}`" width="760px">
      <PublishHistory v-if="hist.open && hist.app" :app="hist.app" @rollback="startRollback" />
    </a-drawer>

    <a-modal v-model:open="rel.open" title="发布到商店" ok-text="上架" destroy-on-close @ok="doRelease">
      <a-input v-model:value="rel.title" placeholder="商店展示名称" />
      <a-textarea v-model:value="rel.summary" placeholder="一句话简介（可选）" :rows="3"
                  style="margin-top: 8px" />
      <p class="zc-modal-tip">
        上架的是当前正式在服版本 {{ rel.app?.current_version ? `（${rel.app.current_version}）` : '' }}，
        含真实执行过的数据变更序列；获批者会得到独立代码与独立数据库的实例。
      </p>
    </a-modal>

    <a-modal v-model:open="rename.open" title="重命名应用" ok-text="保存" destroy-on-close
             class="zc-rename-modal" :ok-button-props="{ disabled: !renameOk }" @ok="doRename">
      <a-input v-model:value="rename.name" placeholder="应用名称" class="zc-rename-input" />
      <p class="zc-modal-tip">只改显示名；应用标识（{{ rename.app?.slug }}）与工程目录保持不变。</p>
    </a-modal>

    <a-modal v-model:open="del.open" title="删除应用" ok-text="确认删除" ok-type="danger" destroy-on-close
             class="zc-del-modal" @ok="doDelete">
      <p class="zc-modal-warn">将删除应用「{{ del.app?.name }}」及其会话记录。</p>
      <p class="zc-modal-tip">
        工程目录保留在 {{ del.app?.root_path }}，数据库 {{ del.app?.slug }}_dev / {{ del.app?.slug }}_prod
        也保留；如需彻底清理，请自行删除目录与库。
      </p>
    </a-modal>

    <workspace-explorer v-if="explorer.app" v-model:open="explorer.open"
                        :workspace-id="explorer.app.workspace_id" :workspace-name="explorer.app.name" />
  </div>
</template>

<style scoped>
.zc-apps { padding: 20px 24px; height: 100%; overflow-y: auto; box-sizing: border-box; }
.zc-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zc-head-left { display: flex; align-items: center; gap: 10px; }
.zc-head h2 { font-size: 18px; font-weight: 600; color: var(--zc-text); margin: 0; }
.zc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.zc-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; flex-shrink: 0; }
/* 标题行拉成 flex：状态点固定、应用名可省略号、规格标签贴着名字（antd 的 .ant-card-head-title 里） */
.zc-card :deep(.ant-card-head-title) { display: flex; align-items: center; overflow: hidden; }
.zc-card-name { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.zc-card-tag { flex-shrink: 0; margin-inline-start: 8px; font-weight: 400; }
.zc-line { margin: 0 0 6px; color: var(--zc-text); font-size: 13px; }
.zc-line.sub { color: var(--zc-text-sub); }
.zc-spin { display: block; margin: 60px auto; }
.zc-pick { border: 1px solid var(--zc-border); border-radius: 6px; padding: 10px 14px; margin-bottom: 10px;
           cursor: pointer; display: flex; flex-direction: column; gap: 4px; }
.zc-pick.on { border-color: var(--zc-primary); background: rgba(91, 122, 157, 0.08); }
.zc-pick span { color: var(--zc-text-sub); font-size: 12px; }
.zc-wizard-foot { display: flex; justify-content: flex-end; margin-top: 16px; }
.zc-tip { color: var(--zc-text-sub); font-size: 12px; margin: 8px 0 0; }
.zc-modal-tip { margin: 8px 0 0; color: var(--zc-text-sub); font-size: 12px; line-height: 1.6; }
.zc-store-tag { margin-inline-start: 8px; font-weight: 400; vertical-align: middle; }

/* 菜单入口用三横（☰）而不是 ⋯ 的圆点：圆点在卡片头部太小、几乎看不见。
   字号抬到 16px、默认透明度 0.7，hover 提亮为主色（与侧栏同一套低饱和语言）。 */
.zc-card-menu-btn { flex-shrink: 0; font-size: 16px; line-height: 1; padding: 3px 4px; cursor: pointer;
                    opacity: 0.7; transition: opacity 0.15s; color: var(--zc-text-sub); }
.zc-card-menu-btn:hover { opacity: 1; color: var(--zc-primary); }
.zc-app-menu { background: #fff; border-radius: 6px; box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
               padding: 4px 0; min-width: 148px; }
.zc-app-menu-item { padding: 6px 14px; font-size: 13px; color: var(--zc-text); cursor: pointer;
                    white-space: nowrap; }
.zc-app-menu-item:hover { background: rgba(91, 122, 157, 0.08); }
.zc-app-menu-danger { color: #b96a6a; }
.zc-app-menu-danger:hover { background: #fdf3f2; }
.zc-modal-warn { margin: 0 0 6px; color: var(--zc-text); font-size: 13px; }
</style>
