<script setup>
// 侧栏边缘手柄：分隔线中部的收起/展开钮 + 全高拖拽热区（双击复位）。
// 只负责外观与交互事件；宽度/收起状态由 ChatView 的 useChatSidePanels 管。
import { computed } from 'vue'
import { LeftOutlined, RightOutlined } from '@ant-design/icons-vue'

const props = defineProps({
  side: { type: String, required: true },
  edge: { type: Number, required: true },
  collapsed: { type: Boolean, default: false },
  dragging: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle', 'dragstart', 'reset'])

const sideName = computed(() => (props.side === 'left' ? '左' : '右'))
const toggleLabel = computed(() => `${props.collapsed ? '展开' : '收起'}${sideName.value}侧栏`)
// 收起态面板宽度为 0，手柄固定贴视口边缘；展开态按传入宽度定位
const edgeStyle = computed(() => {
  const offset = props.collapsed ? 0 : props.edge
  return props.side === 'left' ? { left: `${offset}px` } : { right: `${offset}px` }
})
// 图标永远指向「收起的动作方向」：左栏展开时朝左，收起时朝右（右栏相反）
const toggleIcon = computed(() => {
  const pointLeft = props.side === 'left' ? !props.collapsed : props.collapsed
  return pointLeft ? LeftOutlined : RightOutlined
})
</script>

<template>
  <div
    class="zc-edge"
    :class="[`zc-edge-${side}`, { 'is-collapsed': collapsed, 'is-dragging': dragging }]"
    :style="edgeStyle"
  >
    <div
      v-if="!collapsed"
      class="zc-edge-drag"
      role="separator"
      aria-orientation="vertical"
      :aria-label="`拖动调整${sideName}侧栏宽度，双击恢复默认`"
      @pointerdown="emit('dragstart', $event)"
      @dblclick="emit('reset')"
    />
    <button
      type="button"
      class="zc-edge-toggle"
      :aria-label="toggleLabel"
      :title="toggleLabel"
      @click="emit('toggle')"
    >
      <component :is="toggleIcon" />
    </button>
  </div>
</template>

<style scoped>
.zc-edge {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 0;
  z-index: 20;
}
/* 面板收起/展开时手柄跟着滑动；拖拽中必须实时跟手，不能有过渡 */
.zc-edge-left {
  transition: left 0.2s ease;
}
.zc-edge-right {
  transition: right 0.2s ease;
}
.zc-edge.is-dragging {
  transition: none;
}
.zc-edge-drag {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -3px;
  width: 6px;
  cursor: col-resize;
  z-index: 1;
}
.zc-edge-drag::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 2px;
  width: 2px;
  background: transparent;
  transition: background 0.15s;
}
.zc-edge-drag:hover::after,
.zc-edge.is-dragging .zc-edge-drag::after {
  background: var(--zc-primary);
}
.zc-edge-toggle {
  position: absolute;
  top: 50%;
  left: -9px;
  width: 18px;
  height: 44px;
  padding: 0;
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  color: var(--zc-text-sub);
  background: var(--zc-sidebar-bg);
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  cursor: pointer;
  opacity: 0.55;
  transition: opacity 0.15s, color 0.15s;
  z-index: 2;
}
.zc-edge-toggle:hover,
.zc-edge-toggle:focus-visible {
  opacity: 1;
  color: var(--zc-primary);
}
.zc-edge-toggle:focus-visible {
  outline: 2px solid var(--zc-primary);
  outline-offset: 1px;
}
.zc-edge.is-collapsed .zc-edge-toggle {
  opacity: 1;
}
/* 收起后手柄贴在视口边缘，朝外的一侧去掉圆角，像一页露出的书签 */
.zc-edge-left.is-collapsed .zc-edge-toggle {
  left: 0;
  border-radius: 0 6px 6px 0;
}
.zc-edge-right.is-collapsed .zc-edge-toggle {
  left: -18px;
  border-radius: 6px 0 0 6px;
}
</style>
