<script setup>
// 变更详情的双栏面板：把 sideBySide 的行模型铺成「一行两栏」的网格。
// 为什么每行是一个 grid 容器而不是两个独立滚动列：左右两栏的行必须逐行对齐（这是「对比」的全部意义），
// 分成两个滚动容器就得手工同步滚动，还会在窗口宽度变化时错开；一行两格则天然对齐。
// 折叠段按需展开（expandGutter 只在这一节碰全文），超过上限留下「继续展开」，
// 否则一万行未变更一次铺进 DOM 会把标签页冻住。
import { computed, ref } from 'vue'
import { expandGutter } from '../../utils/sideBySide'

const props = defineProps({
  model: { type: Object, required: true },
  before: { type: String, default: '' },
  after: { type: String, default: '' },
  action: { type: String, default: '' },
  theme: { type: String, default: 'dark' },
  expandLimit: { type: Number, default: 500 },
})

// 展开状态按折叠段自身的形状记（起点 + 长度），不按数组下标：
// 展开一条会把后面所有条目的下标冲掉，用下标当键的话第二条折叠段一点就跳回第一条。
// 续展剩下的那一段有它自己的起点与长度，因此也是一个独立的键——迭代着往下展开。
const expanded = ref({})

const gutterKey = (g) => `g${g.leftStart}-${g.count}`

const rowsView = computed(() => {
  const out = []
  props.model.rows.forEach((r, i) => {
    if (r.kind !== 'gutter') {
      out.push({ type: 'row', key: `r${i}`, row: r })
      return
    }
    // 一段折叠可能被点成「铺 500 行 + 剩一段」，剩的那段继续按同样的规则走，直到展开干净
    let rest = r
    while (rest) {
      const key = gutterKey(rest)
      const open = expanded.value[key]
      if (!open) {
        out.push({ type: 'gutter', key, gutter: rest })
        break
      }
      open.rows.forEach((row, j) => out.push({ type: 'row', key: `${key}#${j}`, row }))
      rest = open.nextGutter
    }
  })
  return out
})

function toggle(key, gutter) {
  if (expanded.value[key]) {
    delete expanded.value[key]
    return
  }
  expanded.value[key] = expandGutter(
    gutter,
    { before: props.before, after: props.after },
    props.expandLimit,
  )
}

const leftLabel = computed(() => (props.action === 'create' ? '变更前不存在' : '变更前'))
const rightLabel = computed(() => (props.action === 'delete' ? '变更后已删除' : '变更后'))

const notice = computed(() => (props.model.status === 'identical'
  ? '本次写入的内容与上一版一致，没有行级变化。'
  : '这份变更的 diff 无法逐行对齐（文件末尾没有换行时，difflib 会把新旧两行挤成一行），下面按两侧全文展示，不假装对齐。'))

// unaligned 兜底：两侧全文逐行铺出来，行号仍是各自的真实行号，只是不再声明「这一行对上那一行」
const plainBefore = computed(() => splitLines(props.before))
const plainAfter = computed(() => splitLines(props.after))

function splitLines(text) {
  const arr = String(text ?? '').split('\n')
  if (arr.length && arr[arr.length - 1] === '') arr.pop()
  return arr
}
</script>

<template>
  <div class="zc-cd" :data-theme="theme">
    <template v-if="model.status === 'ok'">
      <div class="zc-cd-heads">
        <div class="zc-cd-side zc-cd-side-left">{{ leftLabel }}</div>
        <div class="zc-cd-side zc-cd-side-right">{{ rightLabel }}</div>
      </div>
      <div class="zc-cd-body">
        <template v-for="item in rowsView" :key="item.key">
          <button
            v-if="item.type === 'gutter'"
            type="button"
            class="zc-cd-gutter"
            @click="toggle(item.key, item.gutter)"
          >⌄ 展开 {{ item.gutter.count }} 行未变更</button>
          <div v-else class="zc-cd-row">
            <span class="zc-cd-cell" :class="'is-' + item.row.left.cls">
              <span class="zc-cd-no">{{ item.row.left.n ?? '' }}</span>
              <span class="zc-cd-text">{{ item.row.left.text }}</span>
            </span>
            <span class="zc-cd-cell" :class="'is-' + item.row.right.cls">
              <span class="zc-cd-no">{{ item.row.right.n ?? '' }}</span>
              <span class="zc-cd-text">{{ item.row.right.text }}</span>
            </span>
          </div>
        </template>
      </div>
    </template>

    <template v-else>
      <div class="zc-cd-notice">{{ notice }}</div>
      <div v-if="model.status === 'unaligned'" class="zc-cd-plain-wrap">
        <div class="zc-cd-plain">
          <div v-for="(t, i) in plainBefore" :key="`b${i}`" class="zc-cd-row">
            <span class="zc-cd-cell is-plain">
              <span class="zc-cd-no">{{ i + 1 }}</span>
              <span class="zc-cd-text">{{ t }}</span>
            </span>
          </div>
        </div>
        <div class="zc-cd-plain">
          <div v-for="(t, i) in plainAfter" :key="`a${i}`" class="zc-cd-row">
            <span class="zc-cd-cell is-plain">
              <span class="zc-cd-no">{{ i + 1 }}</span>
              <span class="zc-cd-text">{{ t }}</span>
            </span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 变量与工作台同一套口径（ADR-0022 的子树主题），深色是默认档 */
.zc-cd[data-theme='dark'] {
  --zc-bg: #1e1e1e;
  --zc-sidebar-bg: #252526;
  --zc-border: #3c3c3c;
  --zc-text: #cccccc;
  --zc-text-sub: #8c8c8c;
  --zc-add-bg: #1e3a24;
  --zc-add-text: #7ec699;
  --zc-del-bg: #3a1e1e;
  --zc-del-text: #e08a8a;
  --zc-gutter-bg: #252526;
}
.zc-cd[data-theme='light'] {
  --zc-bg: #ffffff;
  --zc-sidebar-bg: #f3f3f3;
  --zc-border: #e0e0e0;
  --zc-text: #333333;
  --zc-text-sub: #6b6b6b;
  --zc-add-bg: #e6ffec;
  --zc-add-text: #1a7f37;
  --zc-del-bg: #ffebe9;
  --zc-del-text: #cf222e;
  --zc-gutter-bg: #f0f0f0;
}
.zc-cd {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--zc-bg);
  color: var(--zc-text);
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12.5px;
}
.zc-cd-heads {
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: 1fr 1fr;
  border-bottom: 1px solid var(--zc-border);
  background: var(--zc-sidebar-bg);
}
.zc-cd-side {
  padding: 5px 10px;
  font-size: 11px;
  color: var(--zc-text-sub);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.zc-cd-side-left {
  border-right: 1px solid var(--zc-border);
}
.zc-cd-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.zc-cd-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  align-items: stretch;
}
.zc-cd-cell {
  display: flex;
  min-width: 0;
  padding-right: 12px;
}
.zc-cd-no {
  flex: 0 0 46px;
  padding: 0 8px 0 6px;
  text-align: right;
  color: var(--zc-text-sub);
  user-select: none;
}
.zc-cd-text {
  flex: 1;
  min-width: 0;
  white-space: pre;
}
.zc-cd-cell.is-del {
  background: var(--zc-del-bg);
  color: var(--zc-del-text);
}
.zc-cd-cell.is-add {
  background: var(--zc-add-bg);
  color: var(--zc-add-text);
}
.zc-cd-cell.is-gap {
  opacity: 0.35;
  background: repeating-linear-gradient(
    -45deg,
    transparent,
    transparent 6px,
    var(--zc-border) 6px,
    var(--zc-border) 7px
  );
}
.zc-cd-gutter {
  display: block;
  width: 100%;
  padding: 3px 10px;
  border: 0;
  border-top: 1px solid var(--zc-border);
  border-bottom: 1px solid var(--zc-border);
  background: var(--zc-gutter-bg);
  color: var(--zc-text-sub);
  font: inherit;
  font-size: 11px;
  text-align: left;
  cursor: pointer;
}
.zc-cd-gutter:hover {
  color: var(--zc-text);
}
.zc-cd-notice {
  padding: 14px 16px;
  font-size: 12.5px;
  color: var(--zc-text-sub);
  border-bottom: 1px solid var(--zc-border);
}
.zc-cd-plain-wrap {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  overflow: auto;
}
.zc-cd-plain {
  min-width: 0;
}
.zc-cd-plain + .zc-cd-plain {
  border-left: 1px solid var(--zc-border);
}
</style>
