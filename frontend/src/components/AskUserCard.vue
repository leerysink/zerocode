<script setup>
// 提问卡：由消息流里的 ask_user 调用行驱动（questions 已在上游整形成统一形状，见 messageModel.js）。
// 单题协议：一次只渲染一道题。
// 待答态（无结果行）——选项与人工回答输入框互斥：打字即清空已选项，点选项即清空输入，提交按非空的
// 一方产出 {options} 或 {answer}；「不再提问，自行决定」把决定权交还模型并继续本轮，不是取消执行。
// 已答态（结果行到达）——原地转只读留痕：题目、全部选项、用户的实际回答都在，输入与按钮一律摘除。
// 卡片状态由结果行决定而非本地字段，所以刷新/切会话/重连后两态都能从历史原样重建。
import { computed, ref, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { askOutcomeOf } from '../stores/messageModel'

const props = defineProps({
  questions: { type: Array, default: () => [] },
  result: { type: Object, default: null },
})
const store = useChatStore()

const question = computed(() => (props.questions || [])[0] || null)
const hasOptions = computed(() => !!(question.value?.options && question.value.options.length))
const multi = computed(() => !!question.value?.multi_select)
const picked = ref(undefined) // 单选 string；多选 string[]；undefined = 未选
const draft = ref('')

// 换题（新提问到达）才清空草稿；同一题的重连补发不得抹掉用户正在输入的内容
watch(() => question.value?.question, () => {
  picked.value = undefined
  draft.value = ''
})

function onDraft(value) {
  draft.value = value
  if (value.trim()) picked.value = multi.value ? [] : undefined
}

function onPick(value) {
  picked.value = value
  if (Array.isArray(value) ? value.length : value) draft.value = ''
}

const answer = computed(() => {
  if (!question.value) return null
  const sel = picked.value
  if (Array.isArray(sel) ? sel.length : sel) {
    return { options: multi.value ? [...sel] : [sel] }
  }
  const text = draft.value.trim()
  return text ? { answer: text } : null
})

function submit() {
  if (answer.value) store.submitAsk([answer.value])
}

// 已答态：单题协议下 answers 恒为一项
const outcome = computed(() => askOutcomeOf(props.result))
const readonly = computed(() => outcome.value.state !== 'pending')
const STATE_TEXT = { cancelled: '已中断', declined: '自行决定', answered: '已回答' }
const stateText = computed(() => STATE_TEXT[outcome.value.state] || '已回答')
const pickedLabels = computed(() => {
  const opts = outcome.value.answers[0]?.options
  return Array.isArray(opts) ? opts : []
})
const freeAnswer = computed(() => {
  const a = outcome.value.answers[0]?.answer
  return typeof a === 'string' ? a : ''
})
function isPicked(label) {
  return pickedLabels.value.includes(label)
}
</script>

<template>
  <div class="zc-msg is-ask">
    <div v-if="question" class="zc-ask" :class="{ 'is-answered': readonly }">
      <!-- 已答态：只读留痕。选项全留，选中项高亮带对勾、未选中项灰显；手输回答原文附在选项区下方 -->
      <template v-if="readonly">
        <div class="zc-ask-state">
          {{ stateText }}<span v-if="outcome.reason" class="zc-ask-state-reason">（{{ outcome.reason }}）</span>
        </div>
        <div class="zc-ask-question">{{ question.question }}</div>
        <div v-if="hasOptions" class="zc-ask-options is-readonly">
          <div
            v-for="opt in question.options" :key="opt.label" class="zc-opt"
            :class="isPicked(opt.label) ? 'is-picked' : 'is-dim'"
          >
            <span class="zc-opt-mark">{{ isPicked(opt.label) ? '✓' : '' }}</span>
            <span class="zc-opt-label">{{ opt.label }}</span>
            <a-tag v-if="opt.recommended" color="blue" class="zc-opt-rec">推荐</a-tag>
            <div v-if="opt.description" class="zc-opt-desc">{{ opt.description }}</div>
          </div>
        </div>
        <div v-if="freeAnswer" class="zc-ask-free-answer">{{ freeAnswer }}</div>
      </template>

      <!-- 待答态：单题可交互表单 -->
      <template v-else>
        <div class="zc-ask-question">{{ question.question }}</div>
        <template v-if="hasOptions">
          <div v-if="multi" class="zc-ask-options">
            <a-checkbox-group :value="picked || []" @update:value="onPick">
              <div v-for="opt in question.options" :key="opt.label" class="zc-opt">
                <a-checkbox :value="opt.label">
                  <span class="zc-opt-label">{{ opt.label }}</span>
                  <a-tag v-if="opt.recommended" color="blue" class="zc-opt-rec">推荐</a-tag>
                </a-checkbox>
                <div v-if="opt.description" class="zc-opt-desc">{{ opt.description }}</div>
              </div>
            </a-checkbox-group>
          </div>
          <a-radio-group v-else :value="picked" class="zc-ask-options" @update:value="onPick">
            <!-- 行点击也走 onPick：点说明行/空白处能选中，且会清掉人工回答的草稿 -->
            <div v-for="opt in question.options" :key="opt.label" class="zc-opt" @click="onPick(opt.label)">
              <a-radio :value="opt.label">
                <span class="zc-opt-label">{{ opt.label }}</span>
                <a-tag v-if="opt.recommended" color="blue" class="zc-opt-rec">推荐</a-tag>
              </a-radio>
              <div v-if="opt.description" class="zc-opt-desc">{{ opt.description }}</div>
            </div>
          </a-radio-group>
          <div class="zc-ask-free-label">或人工回答</div>
        </template>
        <a-textarea
          :value="draft"
          :auto-size="{ minRows: 1, maxRows: 4 }"
          :placeholder="hasOptions ? '输入选项之外的回答…' : '输入你的回答…'"
          aria-label="人工回答"
          class="zc-ask-free"
          @update:value="onDraft"
        />
        <div class="zc-ask-foot">
          <a-button type="link" size="small" class="zc-ask-cancel" @click="store.cancel()">取消执行</a-button>
          <a-button type="link" size="small" class="zc-ask-decline" @click="store.declineAsk()">不再提问，自行决定</a-button>
          <a-button type="primary" :disabled="!answer" @click="submit">提交回答</a-button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.zc-ask {
  width: 100%;
  max-width: 640px;
  background: #fff;
  border: 1px solid var(--zc-border);
  border-left: 3px solid var(--zc-primary);
  border-radius: 8px;
  padding: 12px 14px;
  box-shadow: 0 1px 4px rgba(47, 58, 71, 0.05);
}
/* 已答态：整卡退到背景里，左边框转灰——它是留痕，不是待办 */
.zc-ask.is-answered {
  border-left-color: var(--zc-border);
  background: #fafbfc;
  box-shadow: none;
}
.zc-ask-state {
  font-size: 12px;
  font-weight: 500;
  color: var(--zc-text-sub);
  margin-bottom: 8px;
}
.zc-ask-state-reason { font-weight: 400; }
.zc-ask-question {
  font-weight: 500;
  color: var(--zc-text);
  margin-bottom: 10px;
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-ask-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 6px;
}
.zc-ask-options.is-readonly {
  flex-direction: column;
  gap: 2px;
}
.zc-ask-free-label {
  font-size: 12px;
  color: var(--zc-text-sub);
  margin: 8px 0 4px;
}
.zc-ask-cancel {
  color: var(--zc-text-sub);
}
.zc-ask-cancel:hover {
  color: #a05a5a;
}
.zc-ask-decline {
  color: var(--zc-text-sub);
}
.zc-opt { margin: 2px 0; }
.zc-opt-label { margin-right: 4px; }
.zc-opt-rec { transform: scale(0.85); }
.zc-opt-desc { font-size: 12px; color: var(--zc-text-sub); padding-left: 24px; }
/* 已答态的选项行：对勾占位保证选中/未选中两行文字左对齐 */
.zc-opt-mark {
  display: inline-block;
  width: 16px;
  color: var(--zc-primary);
  font-size: 12px;
}
.zc-opt.is-picked .zc-opt-label { color: var(--zc-text); font-weight: 500; }
.zc-opt.is-dim { opacity: 0.5; }
.zc-opt.is-dim .zc-opt-label { color: var(--zc-text-sub); }
.zc-ask-free { margin-top: 6px; }
.zc-ask-free-answer {
  margin-top: 6px;
  padding: 6px 10px;
  background: #fff;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  color: var(--zc-text);
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
}
.zc-ask-foot {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
</style>
