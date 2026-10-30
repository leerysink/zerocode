<script setup>
// 提问卡（单题版）：一次只渲染一道题；选项与人工回答输入框互斥——
// 打字即清空已选项，点选项即清空输入，提交按非空的一方产出 {options} 或 {answer}。
// 「不再提问，自行决定」把决定权交还模型并继续本轮，不是取消执行。
import { computed, ref, watch } from 'vue'
import { useChatStore } from '../stores/chat'

const props = defineProps({ ask: { type: Object, required: true } })
const store = useChatStore()

const question = computed(() => (props.ask?.questions || [])[0] || null)
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
</script>

<template>
  <div class="zc-msg is-ask">
    <div v-if="question" class="zc-ask">
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
.zc-ask-free { margin-top: 6px; }
.zc-ask-foot {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
</style>
