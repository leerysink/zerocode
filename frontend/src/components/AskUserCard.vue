<script setup>
// 提问卡：agent ask_user 挂起时插入消息流底部。
// 有 options 时显示按钮组（chooseAsk）；a-input 自由输入始终提供（answerAsk，Enter 或按钮提交）；
// 「取消执行」走 cancel（清卡片并复位运行态）。上行失败时卡片保留，draft 不清空以便重试。
import { ref } from 'vue'
import { useChatStore } from '../stores/chat'

defineProps({ ask: { type: Object, required: true } })

const store = useChatStore()
const draft = ref('')

function submitText() {
  const text = draft.value.trim()
  if (!text) return
  store.answerAsk(text)
  if (!store.pendingAsk) draft.value = '' // 仅上行成功（卡片已清）才清输入，失败保留供重试
}
</script>

<template>
  <div class="zc-msg is-ask">
    <div class="zc-ask">
      <div class="zc-ask-question">{{ ask.question }}</div>
      <div v-if="ask.options && ask.options.length" class="zc-ask-options">
        <a-button
          v-for="opt in ask.options"
          :key="opt"
          size="middle"
          @click="store.chooseAsk(opt)"
        >{{ opt }}</a-button>
      </div>
      <div class="zc-ask-input">
        <a-input
          v-model:value="draft"
          size="middle"
          placeholder="或输入自定义回答…"
          @press-enter="submitText"
        />
        <a-button type="primary" size="middle" :disabled="!draft.trim()" @click="submitText">
          回答
        </a-button>
      </div>
      <div class="zc-ask-foot">
        <a-button type="link" size="small" class="zc-ask-cancel" @click="store.cancel()">
          取消执行
        </a-button>
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
.zc-ask-input {
  display: flex;
  gap: 8px;
}
.zc-ask-foot {
  margin-top: 4px;
  text-align: right;
}
.zc-ask-cancel {
  color: var(--zc-text-sub);
}
.zc-ask-cancel:hover {
  color: #a05a5a;
}
</style>
