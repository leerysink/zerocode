<script setup>
// 发布弹层（M2 / T10，规格 04 §5 + 08 UI）：七步进度 + 构建日志 + 完成弹层 + 三步排查指引。
// WS 订阅单实例纪律：应用列表页只在「弹窗开着」时持有 useDeploy——open true→connect /
// false→close，父级 v-if 摘除时 onUnmounted 兜底 close（单测断言不泄漏后台连接）；
// 列表卡片上的 prod 状态靠 5s 轮询双通道刷新。
// 折叠区用原生 details 而非 a-collapse：v4 items 模式塞不进富内容、slot 模式测试桩过重，
// 交互（默认收起 + 可展开）两者等价（对 brief 的一处按实况简化）。
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { Modal, message } from 'ant-design-vue'
import { CheckCircleOutlined, CloseCircleOutlined, CopyOutlined, LoadingOutlined } from '@ant-design/icons-vue'
import { appUrls, publishApp, putConfig, rollbackApp } from '../api'
import { useDeploy } from '../composables/useDeploy'

const props = defineProps({
  app: { type: Object, required: true },
  open: { type: Boolean, default: false },
  mode: { type: String, default: 'publish' },        // publish | rollback
  rollbackVersion: { type: String, default: null },
  autoStart: { type: Boolean, default: false },      // 回滚：历史页确认后打开即触发
})
const emit = defineEmits(['update:open', 'done'])

const STEP_LABELS = {
  preflight: '发布前检查', build: '镜像构建', provision: '数据库供给', migrate: '数据库迁移',
  deploy: '正式栈启动', health: '联合体检', record: '发布记账',
}
const dep = useDeploy(props.app.id)
const urls = ref(null)
const nic = ref('')
const accepting = ref(false)
const tailRef = ref(null)

// publishing 由 WS 事件驱动；accepting 覆盖「受理中」的窗口期（reset 后事件还没来的空档）
const running = computed(() => dep.prodStatus.value === 'publishing' || accepting.value)
const done = computed(() => dep.lastDone.value)
const doneOk = computed(() => !!done.value && done.value.ok)
const doneFail = computed(() => !!done.value && !done.value.ok)
const failText = computed(() => (done.value?.error
  || (props.mode === 'rollback' ? '回滚失败' : '发布失败')).slice(0, 200))
const mainUrl = computed(() => done.value?.url || urls.value?.lan || urls.value?.localhost
  || `http://localhost:${props.app.prod_port}`)

// 失败收口时把仍处 running 的步骤标为红叉（事件流失败不再发 publish_step failed，由前端补语义）
const stepsView = computed(() => Object.entries(STEP_LABELS).map(([key, label]) => {
  let state = dep.steps.value[key] || 'wait'
  if (doneFail.value && state === 'running') state = 'failed'
  return { key, label, state }
}))

const nicOptions = computed(() => (urls.value?.lanCandidates || []).map((c) => ({
  value: c.ip, label: c.docker_hint ? `${c.ip}（虚拟网卡）` : c.ip,
})))

// netsh 命令按 slug+prod_port 现场生成（04 §5 排查指引②）；管理员运行提示写在 UI 文案里
const netshCmd = computed(() => `netsh advfirewall firewall add rule `
  + `name="zerocode-${props.app.slug}-${props.app.prod_port}" dir=in action=allow `
  + `protocol=TCP localport=${props.app.prod_port}`)
const buildText = computed(() => dep.buildTail.value
  .map((t) => (t.kind === 'build' ? '   ' : ' » ') + t.line).join(String.fromCharCode(10)))

let opened = false
let started = false
watch(() => props.open, (v) => {
  if (v) {
    opened = true
    dep.connect()
    if (props.autoStart && props.mode === 'rollback' && !started) {
      started = true
      start()                                  // 历史页确认链：打开即受理，不再二点
    }
  } else if (opened) {
    dep.close()
  }
}, { immediate: true })
onUnmounted(() => { if (opened) dep.close() })
watch(() => dep.lastDone.value, (ev) => {
  if (!ev) return
  emit('done')                              // 收口即通知父级刷新卡片（版本/状态变了）
  if (ev.ok) loadUrls()
})
watch(() => dep.buildTail.value.length, async () => {
  await nextTick()
  if (tailRef.value) tailRef.value.scrollTop = tailRef.value.scrollHeight
})

async function loadUrls() {
  try {
    urls.value = (await appUrls(props.app.id)).data
    nic.value = urls.value.defaultNic || ''
  } catch { urls.value = null }
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); message.success('已复制') }
  catch { message.error('复制失败') }
}
// 失败路径「把错误日志发给 AI」：本波 ChatView 未接 zc_publish_error 消费通道，
// 按 brief 的退路口径只做复制剪贴板 + 引导粘贴（不跳转、不落 localStorage 死键）。
function copyError() {
  const tail = dep.buildTail.value.slice(-80).map((t) => t.line).join(String.fromCharCode(10))
  copyText(`发布失败：${failText.value}${String.fromCharCode(10)}${tail}`)
  message.info('错误日志已复制，粘贴到聊天会话即可让 AI 排查')
}
async function chooseNic(ip) {
  nic.value = ip
  try {
    await putConfig({ 'deploy.default_nic': ip })
    await loadUrls()
  } catch (e) { message.error(e?.response?.data?.detail || '网卡设置失败') }
}
async function publishWith(force) {
  dep.reset()
  accepting.value = true
  try {
    await publishApp(props.app.id, force)
    message.info('发布已受理，进度见下方步骤')
  } catch (e) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 409 && detail && detail.code === 'chat_active') {
      Modal.confirm({
        title: 'AI 正在修改代码', content: detail.message,
        okText: '继续发布', cancelText: '等一等',
        onOk: () => publishWith(true),
      })
    } else {
      message.error(typeof detail === 'string' ? detail : '发布请求未被受理')
    }
  } finally { accepting.value = false }
}
async function start() {
  if (accepting.value || running.value) return          // 受理窗口/进行中不重复触发
  if (props.mode === 'rollback') {
    dep.reset()
    accepting.value = true
    try {
      await rollbackApp(props.app.id, props.rollbackVersion)
      message.info('回滚已受理，进度见下方步骤')
    } catch (e) {
      message.error(e?.response?.data?.detail || '回滚请求未被受理')
      // autoStart 链上没有手动重试按钮：受理失败即收弹层，不停在空进度上空转
      if (props.autoStart) emit('update:open', false)
    } finally { accepting.value = false }
  } else {
    await publishWith(false)
  }
}
</script>

<template>
  <a-modal :open="open" :footer="null" :width="620" destroy-on-close
           :title="`${mode === 'rollback' ? '回滚进度' : '发布进度'} · ${app.name}`"
           @update:open="(v) => emit('update:open', v)">
    <p class="zc-pub-head">
      {{ mode === 'rollback'
        ? `回滚到版本 ${rollbackVersion}：只切程序版本，数据库保持当前状态`
        : `发布将构建镜像并上线到本应用专属端口 ${app.prod_port}（当前版本 ${app.current_version || '未发布'}）` }}
    </p>

    <ul class="zc-steps">
      <li v-for="s in stepsView" :key="s.key" :class="'st-' + s.state">
        <loading-outlined v-if="s.state === 'running'" spin />
        <check-circle-outlined v-else-if="s.state === 'ok'" />
        <close-circle-outlined v-else-if="s.state === 'failed'" />
        <span v-else class="zc-step-dot" />
        {{ s.label }}
      </li>
    </ul>

    <details class="zc-pub-fold">
      <summary>构建日志（{{ dep.buildTail.value.length }} 行）</summary>
      <pre ref="tailRef" class="zc-pub-tail">{{ buildText }}</pre>
    </details>

    <template v-if="doneOk">
      <p class="zc-pub-done">已上线 ✓</p>
      <a class="zc-pub-url" :href="mainUrl" target="_blank"
         @click.prevent="copyText(mainUrl)">{{ mainUrl }} <copy-outlined /></a>
      <p class="zc-pub-sub">本机访问：{{ urls?.localhost || `http://localhost:${app.prod_port}` }}
        <a href="#" @click.prevent="copyText(urls?.localhost || `http://localhost:${app.prod_port}`)">复制</a>
      </p>
      <a-space v-if="nicOptions.length > 1" style="margin: 6px 0">
        <span class="zc-pub-sub">网卡：</span>
        <a-select size="middle" style="width: 220px" :value="nic" :options="nicOptions"
                  @change="chooseNic" />
      </a-space>
      <a-space style="margin: 6px 0">
        <a-button size="middle" @click="copyText(`访问地址：${mainUrl}`)">复制给同事</a-button>
      </a-space>
      <details class="zc-pub-fold">
        <summary>打不开？三步排查</summary>
        <ol class="zc-pub-help">
          <li><b>同网段</b>：两机 IP 前三段需一致（如都在 192.168.1.x），互 ping 不通先查这一点。</li>
          <li><b>防火墙</b>：Windows 防火墙可能阻挡端口 {{ app.prod_port }} 入站。以管理员身份在终端运行
            （或转给 IT）：<span class="zc-code">{{ netshCmd }}
              <a class="zc-code-copy" href="#" @click.prevent="copyText(netshCmd)">
                <copy-outlined />复制命令</a></span></li>
          <li><b>地址完整性</b>：完整地址需包含 <code>http://</code> 前缀与端口号，别只填 IP。</li>
        </ol>
      </details>
    </template>

    <template v-if="doneFail">
      <p class="zc-pub-fail">{{ failText }}</p>
      <a-space>
        <a-button size="middle" :loading="running" @click="start">
          {{ mode === 'rollback' ? '重试回滚' : '重试发布' }}
        </a-button>
        <a-button size="middle" @click="copyError">把错误日志发给 AI</a-button>
      </a-space>
    </template>

    <div v-if="!done && !(autoStart && mode === 'rollback')" class="zc-pub-foot">
      <a-button type="primary" :loading="running" @click="start">
        {{ mode === 'rollback' ? '立即回滚' : '立即发布' }}
      </a-button>
    </div>
  </a-modal>
</template>

<style scoped>
.zc-pub-head { color: var(--zc-text-sub); font-size: 13px; margin: 0 0 12px; }
.zc-steps { list-style: none; padding: 0; margin: 0 0 12px; }
.zc-steps li { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 13px;
               color: var(--zc-text-sub); }
.zc-steps li.st-running { color: var(--zc-text); }
.zc-steps li.st-ok { color: var(--zc-text); }
.zc-steps li.st-failed { color: #b96a6a; }
.zc-step-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
               background: #d3d6db; margin: 0 4px; }
.zc-pub-fold { margin: 8px 0; font-size: 12px; color: var(--zc-text-sub); }
.zc-pub-fold summary { cursor: pointer; user-select: none; }
.zc-pub-tail { max-height: 200px; overflow-y: auto; background: #f7f8fa; border-radius: 4px;
               padding: 8px 10px; font-family: Consolas, monospace; font-size: 11px;
               white-space: pre-wrap; word-break: break-all; }
.zc-pub-done { color: #5b8a72; font-weight: 600; margin: 10px 0 4px; }
.zc-pub-url { display: inline-block; font-size: 17px; font-weight: 600; color: var(--zc-primary);
              text-decoration: none; word-break: break-all; }
.zc-pub-sub { color: var(--zc-text-sub); font-size: 12px; margin: 4px 0; }
.zc-pub-fail { background: #fdf3f2; border: 1px solid #eadcdb; color: #b96a6a; border-radius: 4px;
               padding: 8px 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.zc-code { display: block; background: #f7f8fa; border-radius: 4px; padding: 6px 8px; margin: 4px 0;
           font-family: Consolas, monospace; font-size: 11px; word-break: break-all; }
.zc-code-copy { margin-left: 8px; white-space: nowrap; }
.zc-pub-help li { margin: 6px 0; }
.zc-pub-foot { margin-top: 14px; text-align: right; }
</style>
