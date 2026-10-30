<script setup>
// 中栏对话流：无会话空态（大标题+副标题）/ 消息渲染（user 右侧浅蓝气泡、assistant Markdown 高亮、
// tool 折叠块与预览卡、提问卡、error 红气泡+重试）/ 流式气泡（streamingContent 非空才渲染）/
// 消息流底部插入确认卡（pendingConfirm：写入确认／重启预览）。
// 提问卡不在此列：它是消息流的一部分，由 ask_user 调用行渲染（见 isAskCard）。
// 滚动策略：新消息/新卡片强制滚底；流式增量仅在用户已贴底时跟随，不打断上翻阅读。
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { normalizeMessages } from '../stores/messageModel'
import MarkdownView from './MarkdownView.vue'
import ToolCallBlock from './ToolCallBlock.vue'
import AskUserCard from './AskUserCard.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import TodoCard from './TodoCard.vue'
import PreviewCard from './PreviewCard.vue'
import PageInspectCard from './PageInspectCard.vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  streaming: { type: String, default: '' },
  streamingThinking: { type: String, default: '' },
  pendingConfirm: { type: Object, default: null },
  hasSession: { type: Boolean, default: false },
})

const store = useChatStore()

// Markdown 渲染已抽至 MarkdownView.vue（markdown-it 模块级单例 + 按 content 的 computed 缓存）：
// 流式期间本组件随 token 重渲染，但历史气泡的 content 不变 → 子组件 computed 不重算，零重解析。

// 确认卡 args 摘要：超长截断，避免大文件内容撑爆卡片
const confirmSummary = computed(() => {
  const args = props.pendingConfirm?.args
  if (args == null) return ''
  const text = JSON.stringify(args, null, 2)
  return text.length > 600 ? `${text.slice(0, 600)}\n…（已截断）` : text
})

// 预览工具（规格 2026-10-29-agent-preview-tools §8）：三个工具共用 PreviewCard。
// 名单与后端 llm.PREVIEW_TOOL_NAMES 同名同序；这里刻意不 import 后端常量（前端拿不到），
// 增删预览工具时两处一起改——名字对不上的后果是卡片落回通用折叠块，不会静默出错。
const PREVIEW_TOOL_NAMES = ['preview_status', 'preview_logs', 'preview_restart']
// preview_page 有自己的结果卡（页面文本/截图/视觉诊断）；名单与后端 BROWSER_TOOL_NAMES 同名，
// 漏改的后果是落回通用折叠块，不会静默出错。
const PAGE_TOOL_NAMES = ['preview_page']

// 渲染前统一归一化：实时事件形状与历史行形状都过这一个函数，组件只认归一化后的字段。
// 归一化后一次工具调用恒为一条记录（args 与 result 同体），所以「call 行/result 行」的
// 区分不再存在，卡片判定只看有没有结果。
const view = computed(() => normalizeMessages(props.messages))

function isPreviewTool(m) {
  return PREVIEW_TOOL_NAMES.includes(m.name || '')
}

function isPageTool(m) {
  return PAGE_TOOL_NAMES.includes(m.name || '')
}

// 提问卡：ask_user 调用行一律渲染成卡片，待答/已答由有没有结果行决定（见 AskUserCard）。
// 题目已在归一化阶段整形好；没有题目（模型给了空参数）就落回折叠块，不画一张空卡。
function isAskCard(m) {
  return m.name === 'ask_user' && !!m.questions?.length
}

// 有结果就画终态卡；没结果时只有「消息流最后一条」才画进行中卡——
// 更早的无结果记录只可能是进程崩溃留下的孤儿（正常中断会补写结果行），
// 画成进行中会让人以为又在重启
function isPreviewCard(m, i) {
  return isPreviewTool(m) && !!(m.result || i === view.value.length - 1)
}

// 与 isPreviewCard 同一道闸：归一化后一次调用恒为一条记录，只看有没有结果
function isPageCard(m, i) {
  return isPageTool(m) && !!(m.result || i === view.value.length - 1)
}

// 进度只给「消息流最后一条、还没有结果」的那张卡：历史卡片复活进度条会让人以为又在重启
function liveProgress(m, i) {
  if (m.result) return null
  if (i !== view.value.length - 1) return null
  const p = store.toolProgress
  return p && p.name === (m.name || '') ? p : null
}

// 确认卡按工具换口径：写入确认（args 摘要）／重启预览（后端给的人话 summary）
const isRestartConfirm = computed(() => props.pendingConfirm?.tool === 'preview_restart')
const confirmTitle = computed(() => (isRestartConfirm.value ? '重启预览' : '写入确认'))
const confirmOkText = computed(() => (isRestartConfirm.value ? '确认重启' : '确认写入'))
const confirmNoText = computed(() => (isRestartConfirm.value ? '取消' : '拒绝'))
const confirmSummaryText = computed(() => props.pendingConfirm?.summary || '')

// 自动滚底
const scrollRef = ref(null)
function isNearBottom() {
  const el = scrollRef.value
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 80
}
function scrollToBottom() {
  nextTick(() => {
    const el = scrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}
watch(() => props.messages.length, scrollToBottom) // 新消息（user/tool/error/assistant）强制滚底
watch(() => props.pendingConfirm, scrollToBottom) // 确认卡插入滚底（提问卡已在消息流里，由 messages.length 覆盖）
watch(
  () => props.streaming,
  () => {
    if (isNearBottom()) scrollToBottom()
  }
)
watch(() => props.hasSession, scrollToBottom)
onMounted(scrollToBottom)
</script>

<template>
  <div ref="scrollRef" class="zc-chat">
    <!-- 空态：未选择会话 -->
    <div v-if="!hasSession" class="zc-empty">
      <h1 class="zc-empty-title">探索未至之境</h1>
      <p class="zc-empty-sub">从左侧选择一个会话继续，或新建会话开始探索</p>
    </div>
    <template v-else>
      <div v-if="!view.length && !streaming" class="zc-empty zc-empty-lite">
        <p class="zc-empty-sub">开始你的第一句话吧</p>
      </div>

      <template v-for="(m, i) in view" :key="i">
        <!-- user：右侧浅蓝气泡（图片网格在文字之前；queued = 执行中发出的插话，等下一个工具边界注入） -->
        <div v-if="m.role === 'user'" class="zc-msg is-user">
          <div class="zc-bubble">
            <div v-if="m.attachments && m.attachments.length" class="zc-bubble-images">
              <a-image-preview-group>
                <a-image v-for="a in m.attachments" :key="a.id" :src="a.url" :alt="a.filename" :width="160" />
              </a-image-preview-group>
            </div>
            <span v-if="m.content" class="zc-bubble-text">{{ m.content }}</span>
            <span v-if="m.queued" class="zc-queued">排队中</span>
          </div>
        </div>
        <!-- assistant：思考块（reasoning）+ Markdown 渲染（MarkdownView 按 content 缓存，
             空内容但有思考的历史消息也渲染；纯 tool_calls 空内容行仍不渲染） -->
        <div v-else-if="m.role === 'assistant' && (m.content || m.reasoning)" class="zc-msg is-assistant">
          <div class="zc-assistant">
            <ThinkingBlock v-if="m.reasoning" :thinking="m.reasoning" />
            <MarkdownView v-if="m.content" class="zc-md" :content="m.content" />
          </div>
        </div>
        <!-- notice：压缩完成等系统提示，居中灰字分隔 -->
        <div v-else-if="m.role === 'notice'" class="zc-msg is-notice">
          <span class="zc-notice-text">{{ m.content }}</span>
        </div>
        <!-- tool：todo_write 渲染任务卡；preview_* 渲染预览卡（还要过 isPreviewCard 的闸——
             只有「有结果」或「仍在进行」的那条画卡）；其余落回通用折叠块。
             字段一律是归一化后的 name/args/result，实时与历史同形（见 messageModel.js）。 -->
        <div v-else-if="m.role === 'tool'" class="zc-msg is-tool">
          <AskUserCard v-if="isAskCard(m)" :questions="m.questions" :result="m.result" />
          <TodoCard v-else-if="m.name === 'todo_write' && m.args && m.args.todos" :todos="m.args.todos" />
          <PageInspectCard v-else-if="isPageCard(m, i)" :msg="m" :progress="liveProgress(m, i)" />
          <PreviewCard v-else-if="isPreviewCard(m, i)" :msg="m" :progress="liveProgress(m, i)" />
          <ToolCallBlock v-else :msg="m" />
        </div>
        <!-- error：红色气泡 + 尾部重试（重发最后一条 user 消息） -->
        <div v-else-if="m.role === 'error'" class="zc-msg is-error">
          <div class="zc-error">
            <div class="zc-error-text">{{ m.content }}</div>
            <a-button type="link" danger size="small" @click="store.retry()">重试</a-button>
          </div>
        </div>
      </template>

      <!-- 流式思考：reasoning_delta 累积期间展示 -->
      <div v-if="streamingThinking" class="zc-msg is-assistant">
        <ThinkingBlock :thinking="streamingThinking" streaming />
      </div>

      <!-- 流式气泡：streamingContent 非空才渲染（仅此气泡随 token 重算 Markdown） -->
      <div v-if="streaming" class="zc-msg is-assistant">
        <MarkdownView class="zc-md is-streaming" :content="streaming" />
      </div>

      <!-- 确认卡：写入确认（args 摘要）／重启预览（人话 summary），pendingConfirm 时插入消息流 -->
      <div v-if="pendingConfirm" class="zc-msg is-confirm">
        <div class="zc-confirm">
          <div class="zc-confirm-head">
            {{ confirmTitle }}<span class="zc-confirm-tool">{{ pendingConfirm.tool }}</span>
          </div>
          <p v-if="confirmSummaryText" class="zc-confirm-summary">{{ confirmSummaryText }}</p>
          <pre v-else class="zc-confirm-args">{{ confirmSummary }}</pre>
          <div class="zc-confirm-actions">
            <a-button type="primary" @click="store.confirm(true)">{{ confirmOkText }}</a-button>
            <a-button @click="store.confirm(false)">{{ confirmNoText }}</a-button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.zc-chat {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 24px 28px 16px;
}

/* 空态 */
.zc-empty {
  padding-top: 18vh;
  text-align: center;
}
.zc-empty-lite {
  padding-top: 10vh;
}
.zc-empty-title {
  margin: 0;
  font-size: 30px;
  font-weight: 600;
  letter-spacing: 4px;
  color: var(--zc-text);
}
.zc-empty-sub {
  margin: 12px 0 0;
  font-size: 13px;
  color: var(--zc-text-sub);
}

/* 消息行 */
.zc-msg {
  display: flex;
  margin-bottom: 14px;
  min-width: 0;
}
.zc-msg.is-user {
  justify-content: flex-end;
}
.zc-msg.is-tool,
.zc-msg.is-confirm,
.zc-msg.is-ask {
  width: 100%;
}
.zc-msg.is-tool {
  margin-bottom: 4px;
}
/* assistant 内容容器（思考块 + Markdown 堆叠） */
.zc-assistant {
  width: 100%;
  min-width: 0;
}
/* notice：系统提示条居中 */
.zc-msg.is-notice {
  justify-content: center;
  margin: 4px 0 12px;
}
.zc-notice-text {
  font-size: 12px;
  color: var(--zc-text-sub);
  background: rgba(91, 122, 157, 0.06);
  border-radius: 999px;
  padding: 3px 12px;
}

/* user 浅蓝气泡 */
.zc-bubble {
  max-width: 72%;
  background: #e8f1fb;
  border-radius: 10px 10px 2px 10px;
  padding: 8px 12px;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--zc-text);
}

/* user 气泡内附件图片网格：固定 2 列 160px 缩略图，图片在前文字在后 */
.zc-bubble-images {
  display: grid;
  grid-template-columns: repeat(2, 160px);
  gap: 6px;
  margin-bottom: 6px;
}
.zc-bubble-images :deep(.ant-image) {
  width: 160px;
  height: 160px;
  overflow: hidden;
  border-radius: 8px;
}
.zc-bubble-images :deep(img) {
  width: 160px;
  height: 160px;
  object-fit: cover;
  display: block;
}
.zc-bubble-text {
  display: block;
}

/* 插话排队中的角标：低饱和暖色小标签，只表状态不抢内容 */
.zc-queued {
  margin-left: 8px;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 11px;
  color: #a17c5b;
  background: rgba(161, 124, 91, 0.1);
  white-space: nowrap;
}

.zc-error {
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: 72%;
  background: #fdeeee;
  border: 1px solid #f2d8d8;
  border-radius: 8px;
  padding: 8px 12px;
}
.zc-error-text {
  flex: 1;
  min-width: 0;
  color: #a05a5a;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 写入确认卡 */
.zc-confirm {
  width: 100%;
  max-width: 640px;
  background: #fdfaf3;
  border: 1px solid #ecdfc4;
  border-radius: 8px;
  padding: 12px 14px;
}
.zc-confirm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  color: #8a6d3b;
}
.zc-confirm-tool {
  font-size: 12px;
  font-weight: 400;
  background: rgba(138, 109, 59, 0.1);
  border-radius: 4px;
  padding: 1px 8px;
}
.zc-confirm-args {
  margin: 10px 0;
  max-height: 200px;
  overflow: auto;
  background: #fff;
  border: 1px solid #ecdfc4;
  border-radius: 6px;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--zc-text);
}
.zc-confirm-summary {
  margin: 10px 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--zc-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-confirm-actions {
  display: flex;
  gap: 8px;
}

/* Markdown 正文（v-html 内容需 :deep 穿透） */
.zc-md {
  width: 100%;
  min-width: 0;
  font-size: 14px;
  line-height: 1.75;
  color: var(--zc-text);
  word-break: break-word;
}
.zc-md :deep(p) {
  margin: 0 0 8px;
}
.zc-md :deep(p:last-child) {
  margin-bottom: 0;
}
.zc-md :deep(h1),
.zc-md :deep(h2),
.zc-md :deep(h3),
.zc-md :deep(h4) {
  margin: 14px 0 8px;
  font-size: 15px;
  font-weight: 600;
}
.zc-md :deep(ul),
.zc-md :deep(ol) {
  margin: 0 0 8px;
  padding-left: 22px;
}
.zc-md :deep(li) {
  margin: 2px 0;
}
.zc-md :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 13px;
  background: rgba(91, 122, 157, 0.08);
  border-radius: 4px;
  padding: 1px 5px;
}
.zc-md :deep(pre) {
  margin: 8px 0;
  background: #f6f8fa;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
}
.zc-md :deep(pre code) {
  background: transparent;
  padding: 0;
  font-size: 12.5px;
  line-height: 1.7;
}
.zc-md :deep(blockquote) {
  margin: 8px 0;
  padding: 2px 12px;
  border-left: 3px solid var(--zc-border);
  color: var(--zc-text-sub);
}
.zc-md :deep(a) {
  color: var(--zc-primary);
}
.zc-md :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
}
.zc-md :deep(th),
.zc-md :deep(td) {
  border: 1px solid var(--zc-border);
  padding: 4px 10px;
  font-size: 13px;
}
.zc-md :deep(hr) {
  border: none;
  border-top: 1px solid var(--zc-border);
  margin: 12px 0;
}

/* 流式光标：追加在最后一个块级元素尾部 */
.zc-md.is-streaming :deep(> :last-child)::after {
  content: '▍';
  margin-left: 2px;
  color: var(--zc-primary);
  animation: zc-blink 1.2s steps(2, start) infinite;
}
@keyframes zc-blink {
  to {
    visibility: hidden;
  }
}
</style>
