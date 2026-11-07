<script setup>
// 提问导航条（prompt rail）：中栏对话区最右侧的一列横杠，每根对应一次用户提问。
// 横杠在固定高度的居中盒里等距成簇（不再按内容比例铺满整列），悬停出浮层看全文、点击滚过去。
// 本组件纯展示：几何与高亮由 promptRailModel 算好喂进来，它只负责画与发事件。
import { RAIL_BOX_HEIGHT } from '../stores/promptRailModel'

defineProps({
  marks: { type: Array, default: () => [] },
})
const emit = defineEmits(['select'])
</script>

<template>
  <div class="zc-rail" role="navigation" aria-label="提问导航" :style="{ height: RAIL_BOX_HEIGHT + 'px' }">
    <a-tooltip v-for="m in marks" :key="m.index" placement="left" :overlay-style="{ maxWidth: '440px' }">
      <template #title>
        <div class="zc-rail-tip">{{ m.label }}</div>
      </template>
      <button
        type="button"
        class="zc-rail-mark"
        :class="{ 'is-active': m.active }"
        :style="{ top: m.top + 'px' }"
        :data-prompt-mark="m.index"
        :aria-label="m.label"
        @click="emit('select', m.index)"
      >
        <span class="zc-rail-dash" />
      </button>
    </a-tooltip>
  </div>
</template>

<style scoped>
/* 轨道是与滚动容器并排的一列，不覆盖在滚动区上——压上去会挡住原生滚动条。
   固定高度 + align-self:center，让横杠簇停在右缘中间，而不是被拉满整个聊天列。 */
.zc-rail {
  position: relative;
  flex: none;
  align-self: center;
  width: 24px;
}

.zc-rail-mark {
  position: absolute;
  right: 6px;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: flex-end;
  width: 20px;
  height: 14px;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}

.zc-rail-dash {
  display: block;
  width: 12px;
  height: 2px;
  border-radius: 2px;
  background: rgba(47, 58, 71, 0.18);
  transition: width 0.12s ease, background-color 0.12s ease;
}

.zc-rail-mark:hover .zc-rail-dash,
.zc-rail-mark.is-active .zc-rail-dash {
  width: 18px;
  background: var(--zc-primary);
}

.zc-rail-mark:focus-visible .zc-rail-dash {
  width: 18px;
  background: var(--zc-primary);
  outline: 1px solid var(--zc-primary);
  outline-offset: 2px;
}

/* 浮层正文：完整原文最多 6 行，再多就省略，不让一次长提问撑破屏幕 */
.zc-rail-tip {
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
  max-width: 420px;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
