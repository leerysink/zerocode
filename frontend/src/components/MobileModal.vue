<script setup>
// 移动端弹框（规格 2026-11-03 US-15~US-19）：应用卡面 ☰ 与聊天页「更多」共用同一个组件。
// 自持 <a-modal>（与 PublishModal 同一种所有权形态）：父级只传 app + open，关窗走 update:open。
// 地址一律来自后端 GET /apps/{id}/mobile（ADR-0016）。唯一的加工是把 dev 地址里「打包默认的
// localhost」换成浏览器当前 host——手机扫 localhost 必然失败（CONTEXT「真机预览码」）。
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  CloudDownloadOutlined, CopyOutlined, ExportOutlined, UploadOutlined,
} from '@ant-design/icons-vue'
import { appMobile, uploadMobileApk } from '../api'
import { copyText } from '../utils/clipboard'
import { qrMatrix } from '../utils/qr'

const props = defineProps({
  app: { type: Object, required: true },
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open'])

const info = ref(null)
const loading = ref(false)
const error = ref('')
const env = ref('dev')
const uploading = ref(false)
const uploadPct = ref(0)
const fileInput = ref(null)
let seq = 0

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])
const QR_MARGIN = 2

function fmtBytes(n) {
  if (n == null) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let v = Number(n)
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1 }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`
}

function hostOf(url) {
  try { return new URL(url).hostname.toLowerCase() } catch { return '' }
}

// 后端把 dev 地址投影到 ZEROCODE_PREVIEW_HOST（默认 localhost）。浏览器既然能打开平台，
// 它的 host 就一定能连到同一台机器的端口；但**已显式配置**的宿主优先——只在投影值是
// localhost 时替换，避免把运维配好的 IP 覆盖成另一台机器的地址。
function withLiveHost(url) {
  if (!url) return ''
  try {
    const u = new URL(url)
    if (LOCAL_HOSTS.has(u.hostname.toLowerCase()) && !LOCAL_HOSTS.has(window.location.hostname.toLowerCase())) {
      u.hostname = window.location.hostname
    }
    return u.toString()
  } catch {
    return ''
  }
}

async function load() {
  const my = ++seq
  loading.value = true
  error.value = ''
  try {
    const res = (await appMobile(props.app.id)).data
    if (my === seq) info.value = res
  } catch (e) {
    if (my !== seq) return
    error.value = e?.response?.data?.detail || '移动端信息加载失败'
    message.error(error.value)
  } finally {
    if (my === seq) loading.value = false
  }
}

watch(() => props.open, (v) => {
  if (!v) return
  env.value = 'dev'
  error.value = ''
  uploadPct.value = 0
  load()
}, { immediate: true })

const devUrl = computed(() => withLiveHost(info.value?.dev_url || ''))
const prodUrl = computed(() => info.value?.h5_url || '')
const currentUrl = computed(() => (env.value === 'dev' ? devUrl.value : prodUrl.value))
const devRunning = computed(() => info.value?.status_dev === 'running')
const prodRunning = computed(() => info.value?.status_prod === 'running')
// 打包指引里的「去掉 /h5/ 的部分」仍是对后端投影值的切片，不是前端另拼一套地址。
const prodBase = computed(() => prodUrl.value.replace(/\/h5\/?$/, ''))

const canOpen = computed(() => !!currentUrl.value
  && (env.value === 'dev' ? devRunning.value : prodRunning.value))

const mainQr = computed(() => {
  const text = currentUrl.value
  if (!text) return { reason: env.value === 'dev' ? '预览地址尚未就绪' : '正式地址尚未就绪', matrix: null }
  if (env.value === 'dev') {
    // 未运行优先于 localhost 提示：栈都没起来时说「配宿主 IP」会把人引到错误的下一步
    if (!devRunning.value) return { reason: '预览未运行：先启动预览', matrix: null }
    if (LOCAL_HOSTS.has(hostOf(text))) {
      return { reason: '要真机调试，请把 ZEROCODE_PREVIEW_HOST 配成宿主 IP 后重启平台', matrix: null }
    }
  } else if (!prodRunning.value) {
    return { reason: '正式环境未运行：先发布并启动', matrix: null }
  }
  const matrix = qrMatrix(text)
  return matrix ? { reason: '', matrix } : { reason: '二维码生成失败', matrix: null }
})

const apkUrl = computed(() => info.value?.apk_url || '')
const apkSize = computed(() => info.value?.apk_size ?? null)
const apkMaxMb = computed(() => Math.floor((info.value?.apk_max_bytes || 0) / (1024 * 1024)))
const canUpload = computed(() => prodRunning.value && !uploading.value && !loading.value)
// 没上传过就不出码：US-08 的同一条理由，不给用户一张扫开是 404 的码。
const apkQr = computed(() => {
  if (!prodRunning.value) return { reason: '正式环境未运行', matrix: null }
  if (!apkUrl.value) return { reason: '下载地址尚未就绪', matrix: null }
  if (apkSize.value == null) return { reason: '上传后可扫码下载', matrix: null }
  const matrix = qrMatrix(apkUrl.value)
  return matrix ? { reason: '', matrix } : { reason: '二维码生成失败', matrix: null }
})

const qrViewBox = (matrix) =>
  `${-QR_MARGIN} ${-QR_MARGIN} ${matrix.size + QR_MARGIN * 2} ${matrix.size + QR_MARGIN * 2}`

function fail(text) {
  error.value = text
  message.error(text)
}

async function copyUrl(url) {
  if (!url) return
  if (await copyText(url)) {
    message.success('已复制')
    return
  }
  // 弹框里地址本身就是可选文本，不需要再做一条「就地摊开」的兜底行
  message.error('复制失败，请手动选择地址')
}

function openTab(url, name) {
  const win = window.open(url, name)
  if (!win) message.warning('新标签被拦截了，请允许本站弹出窗口后重试')
}

function openCurrent() {
  if (canOpen.value) openTab(currentUrl.value, `zerocode-mobile-${props.app.id}-${env.value}`)
}

function pickApk() {
  if (canUpload.value) fileInput.value?.click()
}

async function onFile(e) {
  const file = e.target.files?.[0]
  e.target.value = '' // 允许重选同一个文件（上次失败后重试）
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.apk')) return fail('只支持 .apk 文件')
  if (info.value?.apk_max_bytes && file.size > info.value.apk_max_bytes) {
    return fail(`APK 不能超过 ${apkMaxMb.value}MB`)
  }
  uploading.value = true
  uploadPct.value = 0
  error.value = ''
  try {
    await uploadMobileApk(props.app.id, file, (evt) => {
      if (evt.total) uploadPct.value = Math.min(99, Math.round((evt.loaded / evt.total) * 100))
    })
    message.success('APK 已上传')
    await load()
  } catch (err) {
    const detail = err?.response?.data?.detail
    fail(typeof detail === 'string' ? detail : 'APK 上传失败')
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <a-modal
    :open="open"
    :width="760"
    :centered="true"
    :footer="null"
    destroy-on-close
    :title="`移动端 · ${app.name}`"
    @update:open="(v) => emit('update:open', v)"
  >
    <a-spin v-if="loading" class="zc-mobile-spin" />
    <div v-else-if="info" class="zc-mobile">
      <div class="zc-mobile-bar">
        <span class="zc-mobile-hint">预览环境直改直跑；正式环境跑已发布版本。扫码给手机，复制给桌面。</span>
        <a-button size="small" class="zc-mobile-refresh" @click="load">刷新</a-button>
      </div>

      <section class="zc-m-block">
        <div class="zc-m-block-main">
          <div class="zc-m-tabs" role="group" aria-label="环境">
            <button type="button" class="zc-m-tab" :class="{ active: env === 'dev' }"
                    :aria-pressed="env === 'dev'" @click="env = 'dev'">预览环境</button>
            <button type="button" class="zc-m-tab" :class="{ active: env === 'prod' }"
                    :aria-pressed="env === 'prod'" @click="env = 'prod'">正式环境</button>
            <span class="zc-env-state"
                  :class="{ ok: env === 'dev' ? devRunning : prodRunning }">
              {{ env === 'dev'
                ? (devRunning ? '预览运行中' : '预览未运行')
                : (prodRunning ? '正式环境运行中' : '正式环境未运行') }}
            </span>
          </div>

          <div class="zc-m-url">
            <span class="zc-m-k">H5 地址</span>
            <code class="zc-m-url-text">{{ currentUrl || '—' }}</code>
          </div>

          <div class="zc-m-actions">
            <a-button size="small" class="zc-m-copy" :disabled="!currentUrl" @click="copyUrl(currentUrl)">
              <copy-outlined /> 复制地址
            </a-button>
            <a-button size="small" class="zc-m-open" :disabled="!canOpen" @click="openCurrent">
              <export-outlined /> 打开
            </a-button>
          </div>

          <p v-if="env === 'dev'" class="zc-m-tip">
            手机与平台需在同一网络；dev 地址按 ZEROCODE_PREVIEW_HOST 投影，浏览器访问 platform 的 host 会优先用于出码。
          </p>
          <p v-else-if="!prodRunning" class="zc-m-tip warn">
            正式环境未运行：请先发布并启动，网关才会登记这个地址。
          </p>
          <p v-if="error" class="zc-m-tip warn zc-m-error">{{ error }}</p>
        </div>

        <div class="zc-m-qr zc-qr-main">
          <svg v-if="mainQr.matrix" class="zc-qr-svg" :viewBox="qrViewBox(mainQr.matrix)"
               shape-rendering="crispEdges" role="img" aria-label="H5 地址二维码">
            <rect :x="-QR_MARGIN" :y="-QR_MARGIN"
                  :width="mainQr.matrix.size + QR_MARGIN * 2"
                  :height="mainQr.matrix.size + QR_MARGIN * 2" fill="#fff" />
            <path :d="mainQr.matrix.path" fill="#1f2328" />
          </svg>
          <div v-else class="zc-qr-blocked">{{ mainQr.reason }}</div>
          <span class="zc-qr-cap">手机扫码打开</span>
        </div>
      </section>

      <section class="zc-m-block">
        <div class="zc-m-block-main">
          <div class="zc-m-apk-head">
            <span class="zc-m-k">APK 安装包</span>
            <span v-if="apkSize != null" class="zc-apk-badge ok">已上传 {{ fmtBytes(apkSize) }}</span>
            <span v-else class="zc-apk-badge">尚未上传</span>
          </div>

          <p v-if="!prodRunning" class="zc-m-tip warn">
            正式环境未运行：请先发布并启动正式环境，再上传 APK（落点是 prod 的 uploads 卷）。
          </p>
          <template v-else>
            <div class="zc-m-actions">
              <input ref="fileInput" type="file" accept=".apk" class="zc-apk-input" @change="onFile" />
              <a-button size="small" class="zc-apk-pick" :loading="uploading" :disabled="!canUpload"
                        @click="pickApk">
                <upload-outlined /> {{ uploading ? `上传中 ${uploadPct}%` : '上传 APK' }}
              </a-button>
              <template v-if="apkSize != null">
                <a class="zc-apk-get" :href="apkUrl" target="_blank" rel="noopener">
                  <cloud-download-outlined /> 下载
                </a>
                <a-button size="small" class="zc-apk-copy" @click="copyUrl(apkUrl)">
                  <copy-outlined /> 复制下载地址
                </a-button>
              </template>
            </div>
            <div v-if="uploading" class="zc-upload-track">
              <span class="zc-upload-fill" :style="{ width: `${uploadPct}%` }" />
            </div>
          </template>

          <p class="zc-m-tip">
            打包指引：APK 在平台外用 HBuilderX 云打包生成，接口地址必须填正式环境的绝对地址
            （{{ prodBase || '见上方正式地址' }}）——APK 不在同源环境里，相对 /api 不生效。
            SSL 关时该地址是 http://宿主:prod_port 形态，SSL 开时是 https://应用域名。
          </p>
        </div>

        <div class="zc-m-qr zc-qr-apk small">
          <svg v-if="apkQr.matrix" class="zc-qr-svg" :viewBox="qrViewBox(apkQr.matrix)"
               shape-rendering="crispEdges" role="img" aria-label="APK 下载二维码">
            <rect :x="-QR_MARGIN" :y="-QR_MARGIN"
                  :width="apkQr.matrix.size + QR_MARGIN * 2"
                  :height="apkQr.matrix.size + QR_MARGIN * 2" fill="#fff" />
            <path :d="apkQr.matrix.path" fill="#1f2328" />
          </svg>
          <div v-else class="zc-qr-blocked">{{ apkQr.reason }}</div>
          <span class="zc-qr-cap">扫码下载</span>
        </div>
      </section>
    </div>
    <div v-else class="zc-mobile-fail">
      <p>{{ error || '移动端信息不可用' }}</p>
      <a-button size="small" @click="load">重试</a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.zc-mobile-spin { display: block; margin: 48px auto; }
.zc-mobile { display: flex; flex-direction: column; gap: 14px; }

.zc-mobile-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.zc-mobile-hint { font-size: 12px; color: var(--zc-text-sub); }

.zc-m-block { display: flex; gap: 18px; padding: 14px 16px; background: #fff;
              border: 1px solid var(--zc-border); border-radius: 12px;
              box-shadow: 0 1px 4px rgba(47, 58, 71, 0.05); }
.zc-m-block-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 10px; }

.zc-m-tabs { display: flex; align-items: center; gap: 8px; }
.zc-m-tab { border: 1px solid var(--zc-border); background: var(--zc-bg); border-radius: 6px;
            padding: 3px 12px; font-size: 12px; cursor: pointer; color: var(--zc-text-sub);
            transition: all 0.15s; }
.zc-m-tab:hover { color: var(--zc-primary); }
.zc-m-tab.active { background: #fff; border-color: var(--zc-primary); color: var(--zc-primary);
                   font-weight: 600; box-shadow: 0 1px 3px rgba(47, 58, 71, 0.10); }
.zc-env-state { font-size: 11px; color: var(--zc-text-sub); }
.zc-env-state.ok { color: #5b8a72; }

.zc-m-url { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.zc-m-k { flex: 0 0 auto; font-size: 12px; font-weight: 600; color: var(--zc-text-sub); }
.zc-m-url-text { flex: 1 1 auto; min-width: 0; font-size: 12px; color: var(--zc-text);
                 background: var(--zc-bg); border: 1px solid var(--zc-border); border-radius: 6px;
                 padding: 4px 8px; overflow-wrap: anywhere; user-select: all; }

.zc-m-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.zc-m-tip { margin: 0; font-size: 12px; color: var(--zc-text-sub); line-height: 1.6; }
.zc-m-tip.warn { color: #b06a3e; }
.zc-m-error { color: #b96a6a; }

.zc-m-qr { flex: 0 0 auto; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.zc-qr-svg { width: 148px; height: 148px; display: block; border-radius: 8px; }
.zc-m-qr.small .zc-qr-svg { width: 96px; height: 96px; }
.zc-qr-blocked { width: 148px; min-height: 96px; display: flex; align-items: center;
                 justify-content: center; padding: 10px; box-sizing: border-box;
                 font-size: 11px; line-height: 1.5; text-align: center; color: var(--zc-text-sub);
                 background: var(--zc-bg); border: 1px dashed var(--zc-border); border-radius: 8px; }
.zc-m-qr.small .zc-qr-blocked { width: 96px; }
.zc-qr-cap { font-size: 11px; color: var(--zc-text-sub); }

.zc-m-apk-head { display: flex; align-items: center; gap: 10px; }
.zc-apk-badge { font-size: 11px; color: var(--zc-text-sub); background: var(--zc-bg);
                border: 1px solid var(--zc-border); border-radius: 10px; padding: 1px 8px; }
.zc-apk-badge.ok { color: #5b8a72; border-color: rgba(91, 138, 114, 0.35);
                   background: rgba(91, 138, 114, 0.08); }
.zc-apk-input { display: none; }
.zc-apk-get { display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
              color: var(--zc-primary); text-decoration: none; }
.zc-apk-get:hover { text-decoration: underline; }
.zc-upload-track { height: 4px; border-radius: 2px; background: var(--zc-bg); overflow: hidden; }
.zc-upload-fill { display: block; height: 100%; background: var(--zc-primary);
                  transition: width 0.15s; }

.zc-mobile-fail { display: flex; flex-direction: column; align-items: center; gap: 12px;
                  padding: 32px 0; color: var(--zc-text-sub); }
</style>
