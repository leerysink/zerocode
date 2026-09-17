<script>
// Markdown 渲染视图（Task 13 修复：自 ChatMessages 抽出，见 task-13-report 修复记录）。
// markdown-it 配置放在普通 <script> 块——模块级代码仅于首次加载执行一次，整个应用
// 共享同一实例（单例）；<script setup> 内不重复 new MarkdownIt。
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import 'highlight.js/styles/github.css'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true, // 单换行即 <br>，聊天语境更符合直觉
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return `<pre class="hljs"><code>${hljs.highlight(code, { language: lang, ignoreIllegals: true }).value}</code></pre>`
      } catch {
        /* 未知语法退回转义输出 */
      }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(code)}</code></pre>`
  },
})

const renderMd = (text) => md.render(String(text ?? ''))
</script>

<script setup>
// 按 content 缓存：computed 依赖 props.content，值不变则不重算——流式期间
// ChatMessages 整体重渲染时，历史消息 content 不变 → 零重解析（仅流式气泡自身随 token 重算）。
import { computed } from 'vue'

const props = defineProps({
  content: { type: String, default: '' },
})

const html = computed(() => renderMd(props.content))
</script>

<template>
  <div class="md" v-html="html"></div>
</template>
