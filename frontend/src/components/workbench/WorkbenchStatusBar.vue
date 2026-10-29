<script setup>
// 状态栏：语言 / Ln,Col / 选区字符数 / UTF-8 / LF-CRLF / 只读 / 保存态 / 主题切换。
// 换行符按内容判断（后端原样返回），不查编辑器配置——渲染视图与分屏下口径一致。
// 两套主题下都是 VSCode 蓝底白字，只有页面主体跟着工作台主题走。
import { computed } from 'vue'
import { languageLabel, languageOf } from '../../utils/fileTypes'

const props = defineProps({
  path: { type: String, default: '' },
  content: { type: String, default: '' },
  cursor: { type: Object, default: () => ({ line: 1, column: 1, selected: 0 }) },
  readOnly: { type: Boolean, default: false },
  saveText: { type: String, default: '' },
  theme: { type: String, default: 'dark' },
})

defineEmits(['toggle-theme'])

const hasFile = computed(() => !!props.path)
const language = computed(() => languageLabel(languageOf(props.path)))
const line = computed(() => props.cursor?.line ?? 1)
const column = computed(() => props.cursor?.column ?? 1)
const selected = computed(() => props.cursor?.selected ?? 0)
const eol = computed(() => (props.content.includes('\r\n') ? 'CRLF' : 'LF'))
const themeLabel = computed(() => (props.theme === 'dark' ? '浅色' : '深色'))
</script>

<template>
  <div class="zc-wb-status">
    <template v-if="hasFile">
      <span class="zc-wb-lang">{{ language }}</span>
      <span class="zc-wb-cursor">Ln {{ line }}, Col {{ column }}</span>
      <span v-if="selected > 0" class="zc-wb-selected">已选 {{ selected }}</span>
      <span class="zc-wb-encoding">UTF-8</span>
      <span class="zc-wb-eol">{{ eol }}</span>
      <span v-if="readOnly" class="zc-wb-ro">只读</span>
    </template>
    <span class="zc-wb-status-spacer" />
    <span class="zc-wb-save-state">{{ saveText }}</span>
    <button
      type="button"
      class="zc-wb-theme-toggle"
      :title="`切换到${themeLabel}主题`"
      @click="$emit('toggle-theme')"
    >{{ themeLabel }}</button>
  </div>
</template>

<style scoped>
.zc-wb-status {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 24px;
  padding: 0 10px;
  background: #007acc;
  color: #ffffff;
  font-size: 12px;
  user-select: none;
}
.zc-wb-status-spacer {
  flex: 1;
}
.zc-wb-save-state {
  min-height: 16px;
  line-height: 16px;
}
.zc-wb-ro {
  color: #ffe58f;
}
.zc-wb-theme-toggle {
  border: 1px solid rgba(255, 255, 255, 0.55);
  border-radius: 3px;
  background: transparent;
  color: #ffffff;
  font-size: 11px;
  line-height: 16px;
  padding: 0 6px;
  cursor: pointer;
}
.zc-wb-theme-toggle:hover {
  background: rgba(255, 255, 255, 0.16);
}
</style>
