<script setup>
// 提问卡（多问题版）：pendingAsk.questions 逐题渲染；options 题单选/多选+推荐徽标+说明，
// 无 options 题自由输入；submitAsk 一次提交全部答案，未答完禁用。上行失败卡片保留可重试。
import { computed, reactive } from 'vue'
import { useChatStore } from '../stores/chat'

const props = defineProps({ ask: { type: Object, required: true } })
const store = useChatStore()

const questions = computed(() => props.ask?.questions || [])
const drafts = reactive({}) // 未答题的索引 → 文本
const picks = reactive({}) // 有 options 题的索引：单选为 radio 选中 string（初始 undefined），多选为选中 label 数组

function selectionOf(i) {
  if (!picks[i]) picks[i] = []
  return picks[i]
}

// 兼容 radio 产出 string 与 checkbox 产出数组两种形态（brief 裁定版）
function answerOf(q, i) {
  const sel = picks[i]
  if (Array.isArray(sel) ? sel.length : sel) {
    return q.multi_select ? { options: [...sel] } : { options: [sel] }
  }
  const t = (drafts[i] || '').trim()
  return t ? { answer: t } : null
}

const allAnswered = computed(() =>
  questions.value.every((q, i) => answerOf(q, i) !== null))

function submit() {
  if (!allAnswered.value) return
  store.submitAsk(questions.value.map((q, i) => answerOf(q, i)))
}
</script>

<template>
  <div class="zc-msg is-ask">
    <div class="zc-ask">
      <div v-for="(q, i) in questions" :key="i" class="zc-ask-item">
        <div class="zc-ask-question">{{ q.question }}</div>
        <template v-if="q.options && q.options.length">
          <div v-if="q.multi_select" class="zc-ask-options">
            <!-- v-model 不能绑函数调用表达式（Vue 编译报错），拆为 value + update:value 两半 -->
            <a-checkbox-group :value="selectionOf(i)" @update:value="arr => (picks[i] = arr)">
              <div v-for="opt in q.options" :key="opt.label" class="zc-opt">
                <a-checkbox :value="opt.label">
                  <span class="zc-opt-label">{{ opt.label }}</span>
                  <a-tag v-if="opt.recommended" color="blue" class="zc-opt-rec">推荐</a-tag>
                </a-checkbox>
                <div v-if="opt.description" class="zc-opt-desc">{{ opt.description }}</div>
              </div>
            </a-checkbox-group>
          </div>
          <a-radio-group v-else v-model:value="picks[i]" class="zc-ask-options">
            <!-- 行点击赋 string 与 radio-group v-model 同型：点说明行/空白处也能选中，且 answerOf 产出 {options:[label]} -->
            <div v-for="opt in q.options" :key="opt.label" class="zc-opt" @click="picks[i] = opt.label">
              <a-radio :value="opt.label">
                <span class="zc-opt-label">{{ opt.label }}</span>
                <a-tag v-if="opt.recommended" color="blue" class="zc-opt-rec">推荐</a-tag>
              </a-radio>
              <div v-if="opt.description" class="zc-opt-desc">{{ opt.description }}</div>
            </div>
          </a-radio-group>
        </template>
        <a-textarea
          v-else
          v-model:value="drafts[i]"
          :auto-size="{ minRows: 1, maxRows: 4 }"
          placeholder="或输入自定义回答…"
          class="zc-ask-free"
        />
      </div>
      <div class="zc-ask-foot">
        <a-button type="link" size="small" class="zc-ask-cancel" @click="store.cancel()">取消执行</a-button>
        <a-button type="primary" :disabled="!allAnswered" @click="submit">提交回答</a-button>
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
  margin-bottom: 10px;
}
.zc-ask-cancel {
  color: var(--zc-text-sub);
}
.zc-ask-cancel:hover {
  color: #a05a5a;
}
.zc-ask-item { margin-bottom: 14px; }
.zc-ask-item:last-of-type { margin-bottom: 0; }
.zc-opt { margin: 2px 0; }
.zc-opt-label { margin-right: 4px; }
.zc-opt-rec { transform: scale(0.85); }
.zc-opt-desc { font-size: 12px; color: var(--zc-text-sub); padding-left: 24px; }
.zc-ask-free { margin-top: 6px; }
.zc-ask-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
</style>
