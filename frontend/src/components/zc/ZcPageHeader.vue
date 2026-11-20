<script setup>
// 控制台页头薄件：返回 + 标题 + 说明 + 右侧动作槽 + 统计格。
// 只收 props 与插槽，不取数、不含业务判断（ADR-0062 §1）。
import BackButton from '../BackButton.vue'

const props = defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  // 统计格：[{ key, label, value, color? }]，key 缺省时用 label 兜底做 v-for 键
  stats: { type: Array, default: () => [] },
  back: { type: Boolean, default: true },
  backTo: { type: String, default: '/' },
})
</script>

<template>
  <header class="zc-page-head zc-panel" data-test="page-header">
    <div class="zc-page-head-row">
      <div class="zc-page-head-title">
        <back-button v-if="props.back" :to="props.backTo" />
        <h2 data-test="page-title">{{ props.title }}</h2>
        <span v-if="props.subtitle" class="zc-page-head-sub">{{ props.subtitle }}</span>
      </div>
      <div class="zc-page-head-actions">
        <slot name="actions" />
      </div>
    </div>
    <div v-if="props.stats.length" class="zc-page-stats" data-test="page-stats">
      <span v-for="s in props.stats" :key="s.key || s.label" class="zc-page-stat"
            :data-test="`page-stat-${s.key || s.label}`"
            :style="s.color ? { color: s.color } : null">
        <b class="zc-page-stat-value">{{ s.value }}</b>
        <span class="zc-page-stat-label">{{ s.label }}</span>
      </span>
    </div>
  </header>
</template>

<style scoped>
/* 几何一律取 token（ADR-0059 决策 8）：颜色只有一份真源，深浅色同值的是间距与字阶。
   外壳（描边 / 圆角 / 卡底 / 阴影）在 theme.less 的 .zc-panel 里，这里只排布。 */
.zc-page-head { margin-bottom: var(--zc-space-4); padding: var(--zc-space-4); }
/* 返回在面卡里收成描边胶囊：裸文字链接住在卡面上存在感不足，
   与应用页 hero 的返回同一处理（AppsView.vue 的 .zc-hero .zc-back） */
.zc-page-head :deep(.zc-back) { padding: var(--zc-space-hair) var(--zc-space-3);
  border: 1px solid var(--zc-border); border-radius: var(--zc-radius-pill);
  background: var(--zc-bg-card); }
.zc-page-head-row { display: flex; align-items: center; justify-content: space-between;
  gap: var(--zc-space-3); flex-wrap: wrap; }
.zc-page-head-title { display: flex; align-items: baseline; gap: var(--zc-space-3); min-width: 0; }
.zc-page-head-title h2 { margin: 0; font-size: var(--zc-font-lg); font-weight: 600;
  color: var(--zc-text); white-space: nowrap; }
.zc-page-head-sub { font-size: var(--zc-font-sm); color: var(--zc-text-sub); }
.zc-page-head-actions { display: flex; align-items: center; gap: var(--zc-space-2); }
.zc-page-stats { display: flex; flex-wrap: wrap; gap: var(--zc-space-5);
  margin-top: var(--zc-space-3); }
.zc-page-stat { display: flex; flex-direction: column; gap: var(--zc-space-hair);
  min-width: 52px; color: var(--zc-text); }
.zc-page-stat-value { font-size: var(--zc-font-md); font-weight: 600; line-height: 1.1;
  color: inherit; font-variant-numeric: tabular-nums; }
.zc-page-stat-label { font-size: var(--zc-font-xs); color: var(--zc-text-sub); }
</style>
