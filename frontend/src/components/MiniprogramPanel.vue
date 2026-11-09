<script setup>
// 小程序区块（规格 2026-11-09 / ADR-0045）：随移动端弹框出现，卡片 ☰ 与聊天页「更多」共用。
// 只做凭据的绑定/解绑与状态展示；密钥由后端落盘，前端拿不到、也不回显原文。
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CopyOutlined, QrcodeOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import {
  bindMiniprogram, miniprogramPreviewUrl, requestMiniprogramPreview, reuploadMiniprogram,
  unbindMiniprogram,
} from '../api'
import { copyText } from '../utils/clipboard'

const props = defineProps({
  appId: { type: Number, required: true },
  mp: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['refresh'])

const STATUS_TEXT = {
  idle: '未上传',
  building: '构建中',
  uploading: '上传中',
  ready: '已就绪',
  failed: '失败',
}

const appid = ref('')
const keyInput = ref(null)
const keyFile = ref(null)
const saving = ref(false)
const requesting = ref(false)
const requestingUpload = ref(false)
const localError = ref('')

const bound = computed(() => !!props.mp?.bound)
const status = computed(() => props.mp?.status || 'idle')
const statusText = computed(() => STATUS_TEXT[status.value] || status.value)
const busy = computed(() => status.value === 'building' || status.value === 'uploading')
const hasPreview = computed(() => !!props.mp?.preview_available)
const previewUrl = computed(() => miniprogramPreviewUrl(props.appId, props.mp?.preview_at || ''))

// 前置条件（US-25）：没有跳过开关，不满足就禁用上传/预览，只把原因说清
const REASON_TEXT = {
  ssl_off: '网关未启用 HTTPS：小程序只接受备案域名 + 有效 HTTPS 证书（自签不行）',
  no_domain: '缺少应用域名：请先配置 ZEROCODE_GATEWAY_DOMAIN 并使用已备案域名',
  no_credentials: '尚未绑定小程序账号：先绑定 AppID 与上传密钥',
  tls_unreachable: '域名或证书不可用：请确认域名已备案生效且网关使用有效证书',
}
const readyOk = computed(() => props.mp?.readiness?.ok !== false)
const blockedReasons = computed(() => (props.mp?.readiness?.reasons || [])
  .map((r) => REASON_TEXT[r] || r))

const guidance = computed(() => props.mp?.guidance || null)
const STEP_TEXT = {
  dev_version: '微信公众平台「版本管理」选择平台上传的开发版',
  trial_version: '把开发版设为体验版，手机扫码自测',
  submit_review: '提交审核，通过后自行发布（平台不会提审、不会发布）',
}
const steps = computed(() => (guidance.value?.steps || [])
  .map((k) => STEP_TEXT[k]).filter(Boolean))
const badgeClass = computed(() => ({
  ok: status.value === 'ready',
  busy: status.value === 'building' || status.value === 'uploading',
  bad: status.value === 'failed',
}))

function detailText(err, fallback) {
  const detail = err?.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function onKey(e) {
  keyFile.value = e.target.files?.[0] || null
}

async function bind() {
  if (saving.value) return
  localError.value = ''
  if (!appid.value.trim()) {
    localError.value = '请填写小程序 AppID'
    return
  }
  if (!keyFile.value) {
    localError.value = '请选择代码上传密钥文件（.key）'
    return
  }
  saving.value = true
  try {
    await bindMiniprogram(props.appId, appid.value.trim(), keyFile.value)
    message.success('已绑定小程序账号')
    appid.value = ''
    keyFile.value = null
    if (keyInput.value) keyInput.value.value = ''
    emit('refresh')
  } catch (err) {
    localError.value = detailText(err, '绑定失败')
    message.error(localError.value)
  } finally {
    saving.value = false
  }
}

async function copyGuidance(value, okText) {
  if (!value) return
  if (await copyText(value)) {
    message.success(okText)
    return
  }
  message.error('复制失败，请手动选择文本')
}

async function reupload() {
  if (busy.value || requestingUpload.value) return
  requestingUpload.value = true
  localError.value = ''
  try {
    await reuploadMiniprogram(props.appId)
    message.success('已开始上传开发版')
    emit('refresh')
  } catch (err) {
    localError.value = detailText(err, '重新上传失败')
    message.error(localError.value)
  } finally {
    requestingUpload.value = false
  }
}

async function genPreview() {
  if (busy.value || requesting.value) return
  requesting.value = true
  localError.value = ''
  try {
    await requestMiniprogramPreview(props.appId)
    message.success('已开始生成预览码')
    emit('refresh')
  } catch (err) {
    localError.value = detailText(err, '生成预览码失败')
    message.error(localError.value)
  } finally {
    requesting.value = false
  }
}

async function unbind() {
  if (saving.value) return
  saving.value = true
  localError.value = ''
  try {
    await unbindMiniprogram(props.appId)
    message.success('已解绑小程序账号')
    emit('refresh')
  } catch (err) {
    localError.value = detailText(err, '解绑失败')
    message.error(localError.value)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="zc-mp">
    <div class="zc-mp-head">
      <span class="zc-mp-k">微信小程序</span>
      <span class="zc-mp-badge" :class="badgeClass">{{ statusText }}</span>
    </div>

    <template v-if="bound">
      <div v-if="!readyOk" class="zc-mp-blocker">
        <p v-for="(reason, i) in blockedReasons" :key="i" class="zc-mp-blocker-line">{{ reason }}</p>
      </div>

      <div class="zc-mp-row">
        <span class="zc-mp-k">开发版</span>
        <span v-if="mp.version" class="zc-mp-meta">版本 {{ mp.version }}</span>
        <span v-if="mp.uploaded_at" class="zc-mp-meta">上传于 {{ fmtTime(mp.uploaded_at) }}</span>
        <a-button size="small" class="zc-mp-upload" :loading="requestingUpload"
                  :disabled="busy || requestingUpload || !readyOk" @click="reupload">
          <reload-outlined /> 重新上传
        </a-button>
      </div>
      <p class="zc-mp-tip">
        不改代码、不重新发布也能覆盖一次开发版（版本号仍取当前发布版本，不产生预览码）。
      </p>

      <div class="zc-mp-row">
        <span class="zc-mp-k">AppID</span>
        <code class="zc-mp-appid">{{ mp.appid_masked }}</code>
        <span v-if="mp.key_uploaded_at" class="zc-mp-meta">
          密钥上传于 {{ fmtTime(mp.key_uploaded_at) }}
        </span>
        <a-button size="small" class="zc-mp-unbind" :loading="saving" :disabled="saving"
                  @click="unbind">解绑</a-button>
      </div>
      <p v-if="status === 'building'" class="zc-mp-tip">
        <template v-if="mp.job_kind === 'preview'">
          正在生成预览码，每 3 秒自动刷新；关闭弹框不影响任务。
        </template>
        <template v-else>
          平台正在编译并预检（不产生开发版），每 3 秒自动刷新；关闭弹框不影响任务。
        </template>
      </p>
      <p v-else-if="status === 'uploading'" class="zc-mp-tip">
        正在上传开发版到微信，每 3 秒自动刷新；失败不影响已发布的 H5。
      </p>

      <div class="zc-mp-preview">
        <div class="zc-mp-preview-main">
          <div class="zc-mp-row">
            <span class="zc-mp-k">预览码</span>
            <span v-if="mp.preview_at" class="zc-mp-meta">生成于 {{ fmtTime(mp.preview_at) }}</span>
            <a-button size="small" class="zc-mp-preview-btn" :loading="requesting"
                      :disabled="busy || requesting || !readyOk" @click="genPreview">
              <reload-outlined v-if="hasPreview" />
              <qrcode-outlined v-else />
              {{ hasPreview ? '重新生成' : '生成预览码' }}
            </a-button>
          </div>
          <p class="zc-mp-tip">
            预览码短时有效、仅项目成员可扫，过期请重新生成；只生成码，不产生开发版。
          </p>
        </div>
        <div v-if="hasPreview" class="zc-mp-qr">
          <img class="zc-mp-qr-img" :src="previewUrl" alt="小程序预览码" />
          <span class="zc-mp-meta">手机微信扫一扫</span>
        </div>
      </div>

      <div v-if="guidance" class="zc-mp-guide">
        <div class="zc-mp-row">
          <span class="zc-mp-k">request 合法域名</span>
          <code class="zc-mp-guide-val">{{ guidance.request_domain || '—' }}</code>
          <a-button size="small" class="zc-mp-copy-domain" :disabled="!guidance.request_domain"
                    @click="copyGuidance(guidance.request_domain, '域名已复制')">
            <copy-outlined /> 复制
          </a-button>
        </div>
        <div class="zc-mp-row">
          <span class="zc-mp-k">平台出网 IP</span>
          <code class="zc-mp-guide-val">{{ guidance.egress_ip || '获取失败，请手动确认' }}</code>
          <a-button size="small" class="zc-mp-copy-ip" :disabled="!guidance.egress_ip"
                    @click="copyGuidance(guidance.egress_ip, 'IP 已复制')">
            <copy-outlined /> 复制
          </a-button>
        </div>
        <p class="zc-mp-tip">
          每个应用域名需在该小程序后台逐个添加 request 合法域名（不支持通配符）；
          平台出网 IP 需加入上传 IP 白名单。
        </p>
        <ol class="zc-mp-steps">
          <li v-for="(step, i) in steps" :key="i">{{ step }}</li>
        </ol>
        <a class="zc-mp-console" :href="guidance.console_url" target="_blank" rel="noopener">
          去微信公众平台
        </a>
      </div>

      <p v-if="mp.error" class="zc-mp-error">失败：{{ mp.error }}</p>
    </template>

    <template v-else>
      <p class="zc-mp-tip">
        在微信公众平台「开发管理 → 开发设置」下载代码上传密钥，并把平台出网 IP 加入上传 IP 白名单。
        密钥只保存在平台服务器，不会回显，也不进 AI 上下文。
      </p>
      <div class="zc-mp-form">
        <input v-model="appid" class="zc-mp-appid-input" placeholder="小程序 AppID（wx 开头）" />
        <input ref="keyInput" type="file" accept=".key" class="zc-mp-key-input" @change="onKey" />
        <a-button size="small" class="zc-mp-bind" :loading="saving" :disabled="saving"
                  @click="bind">绑定</a-button>
      </div>
    </template>

    <p v-if="localError" class="zc-mp-error">{{ localError }}</p>
  </section>
</template>

<style scoped>
.zc-mp { display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; background: #fff;
         border: 1px solid var(--zc-border); border-radius: 12px;
         box-shadow: 0 1px 4px rgba(47, 58, 71, 0.05); }
.zc-mp-head { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.zc-mp-k { font-size: 12px; font-weight: 600; color: var(--zc-text-sub); }
.zc-mp-badge { font-size: 11px; color: var(--zc-text-sub); background: var(--zc-bg);
               border: 1px solid var(--zc-border); border-radius: 10px; padding: 1px 8px; }
.zc-mp-badge.ok { color: #5b8a72; border-color: rgba(91, 138, 114, 0.35);
                  background: rgba(91, 138, 114, 0.08); }
.zc-mp-badge.busy { color: #9a7b3e; border-color: rgba(154, 123, 62, 0.35);
                    background: rgba(154, 123, 62, 0.08); }
.zc-mp-badge.bad { color: #b96a6a; border-color: rgba(185, 106, 106, 0.35);
                   background: rgba(185, 106, 106, 0.08); }
.zc-mp-meta { font-size: 11px; color: var(--zc-text-sub); }
.zc-mp-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.zc-mp-appid { font-size: 12px; color: var(--zc-text); background: var(--zc-bg);
               border: 1px solid var(--zc-border); border-radius: 6px; padding: 2px 8px;
               font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.zc-mp-form { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.zc-mp-appid-input { width: 230px; font-size: 12px; padding: 3px 8px; border-radius: 6px;
                     border: 1px solid var(--zc-border); background: var(--zc-bg);
                     color: var(--zc-text); }
.zc-mp-key-input { font-size: 11px; color: var(--zc-text-sub); max-width: 240px; }
.zc-mp-tip { margin: 0; font-size: 12px; color: var(--zc-text-sub); line-height: 1.6; }
.zc-mp-preview { display: flex; align-items: flex-start; gap: 16px; }
.zc-mp-preview-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.zc-mp-qr { flex: 0 0 auto; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.zc-mp-qr-img { width: 132px; height: 132px; display: block; border-radius: 8px;
                border: 1px solid var(--zc-border); background: #fff; object-fit: contain; }
.zc-mp-blocker { display: flex; flex-direction: column; gap: 4px; padding: 8px 10px;
                 border: 1px solid rgba(185, 106, 106, 0.35); border-radius: 8px;
                 background: rgba(185, 106, 106, 0.06); }
.zc-mp-blocker-line { margin: 0; font-size: 12px; color: #b96a6a; line-height: 1.6; }
.zc-mp-guide { display: flex; flex-direction: column; gap: 8px; padding-top: 10px;
               border-top: 1px dashed var(--zc-border); }
.zc-mp-guide-val { font-size: 12px; color: var(--zc-text); background: var(--zc-bg);
                   border: 1px solid var(--zc-border); border-radius: 6px; padding: 2px 8px;
                   font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                   overflow-wrap: anywhere; user-select: all; }
.zc-mp-steps { margin: 0; padding-left: 18px; font-size: 12px; color: var(--zc-text-sub);
               line-height: 1.8; }
.zc-mp-console { font-size: 12px; color: var(--zc-primary); text-decoration: none; }
.zc-mp-console:hover { text-decoration: underline; }
.zc-mp-error { margin: 0; font-size: 12px; color: #b96a6a; }
</style>
