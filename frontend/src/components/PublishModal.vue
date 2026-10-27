<script setup>
// 发布弹层（M2 / T10，规格 04 §5 + 08 UI）：七步进度 + 构建日志 + 完成弹层（域名访问）。
// WS 订阅单实例纪律：应用列表页只在「弹窗开着」时持有 useDeploy——open true→connect /
// false→close，父级 v-if 摘除时 onUnmounted 兜底 close（单测断言不泄漏后台连接）；
// 列表卡片上的 prod 状态靠 5s 轮询双通道刷新。
// 折叠区用原生 details 而非 a-collapse：v4 items 模式塞不进富内容、slot 模式测试桩过重，
// 交互（默认收起 + 可展开）两者等价（对 brief 的一处按实况简化）。
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { Modal, message } from 'ant-design-vue'
import { CheckCircleOutlined, CloseCircleOutlined, CopyOutlined, LoadingOutlined } from '@ant-design/icons-vue'
import { appUrls, publishApp, rollbackApp, scaffoldBackfill } from '../api'
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
const accepting = ref(false)
const tailRef = ref(null)

// publishing 由 WS 事件驱动；accepting 覆盖「受理中」的窗口期（reset 后事件还没来的空档）
const running = computed(() => dep.prodStatus.value === 'publishing' || accepting.value)
const done = computed(() => dep.lastDone.value)
const doneOk = computed(() => !!done.value && done.value.ok)
const doneFail = computed(() => !!done.value && !done.value.ok)
const failText = computed(() => (done.value?.error
  || (props.mode === 'rollback' ? '回滚失败' : '发布失败')).slice(0, 200))
const isDestructive = computed(() => done.value?.code === 'schema_destructive')
const destructiveList = computed(() => (Array.isArray(done.value?.destructive)
  ? done.value.destructive : []))
// 地址只有两个来源：发布完成事件里的 url，与 app_urls 接口的 domain。两个都拿不到就是空串——
// 不在前端拼域名兜底（ADR-0016：域名的事实来源只在后端一处，前端再放一份常量必然漂移）。
// 更要紧的是：后端在 slug 缺位时**故意**回空串而不是拼一个必然无效的地址，前端一兜就把这个
// 刻意留白填成了假地址，而它在界面上和真地址长得一模一样。
const mainUrl = computed(() => done.value?.url || urls.value?.domain || '')

// 失败收口时把仍处 running 的步骤标为红叉（事件流失败不再发 publish_step failed，由前端补语义）
const stepsView = computed(() => Object.entries(STEP_LABELS).map(([key, label]) => {
  let state = dep.steps.value[key] || 'wait'
  if (doneFail.value && state === 'running') state = 'failed'
  return { key, label, state }
}))

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
async function publishWith(force, confirmDestructive = false) {
  dep.reset()
  accepting.value = true
  try {
    await publishApp(props.app.id, force, confirmDestructive)
    message.info('发布已受理，进度见下方步骤')
  } catch (e) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 409 && detail && detail.code === 'chat_active') {
      Modal.confirm({
        title: 'AI 正在修改代码', content: detail.message,
        okText: '继续发布', cancelText: '等一等',
        onOk: () => publishWith(true, confirmDestructive),
      })
    } else {
      message.error(typeof detail === 'string' ? detail : '发布请求未被受理')
    }
  } finally { accepting.value = false }
}
async function confirmPublish() { await publishWith(true, true) }
async function genSkeleton() {
  try {
    const { data } = await scaffoldBackfill(props.app.id)
    message.success(`已写入 sqlchange/${data.filename}，请在 AI 会话补实回填逻辑后重新发布`)
  } catch (e) {
    message.error(e?.response?.data?.detail || '生成回填骨架失败')
  }
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
      <template v-if="mainUrl">
        <a class="zc-pub-url" :href="mainUrl" target="_blank"
           @click.prevent="copyText(mainUrl)">{{ mainUrl }} <copy-outlined /></a>
        <a-space style="margin: 6px 0">
          <a-button size="middle" @click="copyText(`访问地址：${mainUrl}`)">复制给同事</a-button>
        </a-space>
      </template>
      <p v-else class="zc-pub-nourl">
        已上线，但访问地址没取到。可在应用卡片上重试；若反复如此，检查平台与网关的
        <code>GATEWAY_DOMAIN</code> 是否一致、以及该应用 slug 是否为空。
      </p>
    </template>

    <template v-if="doneFail && isDestructive">
      <p class="zc-pub-fail">检测到破坏性结构变更，已阻断发布。请确认下列变更系有意为之：</p>
      <ul class="zc-pub-destr">
        <li v-for="(item, i) in destructiveList" :key="i">{{ item }}</li>
        <li v-if="!destructiveList.length">（无明细，见上方说明）</li>
      </ul>
      <a-space>
        <a-button size="middle" @click="genSkeleton">生成回填骨架</a-button>
        <a-button size="middle" type="primary" :loading="running" @click="confirmPublish">
          我已确认风险，继续发布
        </a-button>
      </a-space>
    </template>
    <template v-else-if="doneFail">
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
.zc-pub-nourl { margin: 6px 0; padding: 8px 10px; font-size: 12px; color: var(--zc-text-sub);
                background: #f7f8fa; border: 1px solid #e6e8eb; border-radius: 4px; }
.zc-pub-nourl code { font-family: Consolas, monospace; }
.zc-pub-fail { background: #fdf3f2; border: 1px solid #eadcdb; color: #b96a6a; border-radius: 4px;
               padding: 8px 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.zc-pub-destr { margin: 6px 0 10px; padding-left: 20px; font-size: 12px; color: #b96a6a; }
.zc-pub-destr li { margin: 2px 0; }
.zc-pub-foot { margin-top: 14px; text-align: right; }
</style>
