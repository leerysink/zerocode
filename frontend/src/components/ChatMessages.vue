<script setup>
// 中栏对话流：无会话空态（大标题+副标题）/ 消息渲染（user 右侧浅蓝气泡、assistant Markdown 高亮、
// tool 折叠块、error 红气泡+重试）/ 流式气泡（streamingContent 非空才渲染）/ 消息流底部
// 插入写入确认卡（pendingConfirm）与提问卡（pendingAsk）。
// 滚动策略：新消息/新卡片强制滚底；流式增量仅在用户已贴底时跟随，不打断上翻阅读。
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import MarkdownView from './MarkdownView.vue'
import ToolCallBlock from './ToolCallBlock.vue'
import AskUserCard from './AskUserCard.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import TodoCard from './TodoCard.vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  streaming: { type: String, default: '' },
  streamingThinking: { type: String, default: '' },
  pendingAsk: { type: Object, default: null },
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
watch(() => [props.pendingAsk, props.pendingConfirm], scrollToBottom) // 卡片插入滚底
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
      <div v-if="!messages.length && !streaming" class="zc-empty zc-empty-lite">
        <p class="zc-empty-sub">开始你的第一句话吧</p>
      </div>

      <template v-for="(m, i) in messages" :key="i">
        <!-- user：右侧浅蓝气泡（queued = 执行中发出的插话，等下一个工具边界注入） -->
        <div v-if="m.role === 'user'" class="zc-msg is-user">
          <div class="zc-bubble">
            {{ m.content }}
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
        <!-- tool：todo_write 调用处渲染任务卡（实时 tool_call 消息才有 args.todos；
             tool_result 与历史消息无 args，落回折叠工具块，避免同一调用渲染两张卡） -->
        <div v-else-if="m.role === 'tool'" class="zc-msg is-tool">
          <TodoCard v-if="m.name === 'todo_write' && m.args && m.args.todos" :todos="m.args.todos" />
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

      <!-- 写入确认卡：pendingConfirm 时插入消息流 -->
      <div v-if="pendingConfirm" class="zc-msg is-confirm">
        <div class="zc-confirm">
          <div class="zc-confirm-head">
            写入确认<span class="zc-confirm-tool">{{ pendingConfirm.tool }}</span>
          </div>
          <pre class="zc-confirm-args">{{ confirmSummary }}</pre>
          <div class="zc-confirm-actions">
            <a-button type="primary" @click="store.confirm(true)">确认写入</a-button>
            <a-button @click="store.confirm(false)">拒绝</a-button>
          </div>
        </div>
      </div>

      <!-- 提问卡：pendingAsk 时插入消息流底部 -->
      <AskUserCard v-if="pendingAsk" :ask="pendingAsk" />
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
