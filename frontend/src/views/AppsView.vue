<script setup>
// 应用页（M1 最小版，规格 08 FR8-01/FR8-02 的基础实现）：卡片列表 + 三步向导 + 预览抽屉
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  AppstoreOutlined, CloudUploadOutlined, CopyOutlined, EyeOutlined,
  PlayCircleOutlined, PoweroffOutlined, ReloadOutlined, RocketOutlined,
} from '@ant-design/icons-vue'
import {
  createApp, devRestart, devStart, devStatus, devStop, listApps, validateAppSlug,
} from '../api'
import { SMALL_STACKS, slugFromName, validateForm } from '../utils/appForm'

const apps = ref([])
const loading = ref(true)
const showWizard = ref(false)
const current = ref(0)
const preview = reactive({ open: false, app: null, url: '', status: '', nonce: 0 })
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
async function copyUrl(url) {
  await navigator.clipboard.writeText(url)
  message.success('已复制')
}
</script>

<template>
  <div class="zc-apps">
    <div class="zc-head">
      <h2><appstore-outlined /> 我的应用</h2>
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
          {{ a.name }}
        </template>
        <template #extra>
          <a-tag>{{ a.scale === 'large' ? '大型' : '小型' }} · {{ a.stack }}</a-tag>
        </template>
        <p class="zc-line">
          预览：{{ statusText(a.status_dev) }} · 端口 {{ a.dev_port }}
          <a v-if="a.status_dev === 'running'" :href="`http://localhost:${a.dev_port}`"
             target="_blank">打开</a>
        </p>
        <p class="zc-line sub">正式发布：M2 即将上线 <cloud-upload-outlined /></p>
        <a-space wrap>
          <a-button size="middle" @click="openPreview(a)"><eye-outlined /> 预览</a-button>
          <a-button size="middle" @click="toggleDev(a)">
            <poweroff-outlined v-if="a.status_dev === 'running'" />
            <play-circle-outlined v-else />
            {{ a.status_dev === 'running' ? '停止' : '启动预览' }}
          </a-button>
          <a-button size="middle" @click="restartDev(a)">重启</a-button>
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
  </div>
</template>

<style scoped>
.zc-apps { padding: 20px 24px; height: 100%; overflow-y: auto; box-sizing: border-box; }
.zc-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zc-head h2 { font-size: 18px; font-weight: 600; color: var(--zc-text); margin: 0; }
.zc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.zc-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }
.zc-line { margin: 0 0 6px; color: var(--zc-text); font-size: 13px; }
.zc-line.sub { color: var(--zc-text-sub); }
.zc-spin { display: block; margin: 60px auto; }
.zc-pick { border: 1px solid var(--zc-border); border-radius: 6px; padding: 10px 14px; margin-bottom: 10px;
           cursor: pointer; display: flex; flex-direction: column; gap: 4px; }
.zc-pick.on { border-color: var(--zc-primary); background: rgba(91, 122, 157, 0.08); }
.zc-pick span { color: var(--zc-text-sub); font-size: 12px; }
.zc-wizard-foot { display: flex; justify-content: flex-end; margin-top: 16px; }
.zc-tip { color: var(--zc-text-sub); font-size: 12px; margin: 8px 0 0; }
</style>
