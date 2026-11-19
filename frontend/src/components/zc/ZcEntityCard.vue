<script setup>
// 控制台实体卡骨架薄件：统一「封面 / 标题行 / 摘要两行截断 / 元信息 / 动作 / 页脚」的节奏。
// 等高与截断**内建**、不做成 prop（ADR-0062 §6）：四页只要用这件就同形，
// 一旦留成开关，就会出现两页等高两页不等高，看久了仍是四个产品。
// 只收 props 与插槽，不取数、不含业务判断。
defineProps({
  // 左缘 3px 状态轨颜色；传空则不出轨
  rail: { type: String, default: '' },
  // 已下架/失效条目的整卡降饱和
  muted: { type: Boolean, default: false },
  // 封面位是否可点（可点才给指针与 hover 反馈，避免「看着能点其实没反应」）
  coverClickable: { type: Boolean, default: false },
})

defineEmits(['cover-click'])
</script>

<template>
  <article class="zc-entity-card" :class="{ 'is-muted': muted }"
           :style="rail ? { '--zc-rail': rail } : null">
    <div v-if="$slots.cover" class="zc-entity-cover"
         :class="{ 'is-clickable': coverClickable }" data-test="entity-cover"
         @click="coverClickable ? $emit('cover-click') : null">
      <slot name="cover" />
      <div v-if="$slots.badge" class="zc-entity-badge"><slot name="badge" /></div>
    </div>

    <div class="zc-entity-body">
      <div class="zc-entity-titlerow">
        <h3 class="zc-entity-name" data-test="entity-title"><slot name="title" /></h3>
        <span v-if="$slots.tags" class="zc-entity-tags"><slot name="tags" /></span>
        <span v-if="$slots.menu" class="zc-entity-menu"><slot name="menu" /></span>
      </div>

      <slot />

      <p v-if="$slots.summary" class="zc-entity-summary" data-test="entity-summary">
        <slot name="summary" />
      </p>

      <div v-if="$slots.meta" class="zc-entity-meta"><slot name="meta" /></div>
      <div v-if="$slots.actions" class="zc-entity-actions"><slot name="actions" /></div>
      <div v-if="$slots.footer" class="zc-entity-footer"><slot name="footer" /></div>
    </div>
  </article>
</template>

<style scoped>
/* 等高由网格的 align-items: stretch 给（调用点负责列宽），卡内靠 meta 之前的
   margin-top:auto 把底部块推齐——所以「卡高一致、底部动作同一条线」是这件的内建行为。 */
.zc-entity-card { position: relative; display: flex; flex-direction: column; min-width: 0;
  overflow: hidden; background: var(--zc-bg-card); border: 1px solid var(--zc-border);
  border-radius: var(--zc-radius-card);
  box-shadow: var(--zc-shadow-1) rgba(var(--zc-shadow-rgb), 0.05);
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s; }
.zc-entity-card:hover { border-color: rgba(var(--zc-primary-rgb), 0.45);
  box-shadow: var(--zc-shadow-1) rgba(var(--zc-shadow-rgb), 0.12);
  transform: translateY(-2px); }
/* 左缘状态轨：颜色由调用点经 --zc-rail 注入，自带圆角故卡根不需要 overflow 裁剪对齐 */
.zc-entity-card::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0;
  width: 3px; background: var(--zc-rail, transparent); }
.zc-entity-card.is-muted .zc-entity-cover { filter: grayscale(0.7); opacity: 0.8; }

.zc-entity-cover { position: relative; aspect-ratio: 16 / 9; overflow: hidden;
  background: var(--zc-bg-cover); }
.zc-entity-cover.is-clickable { cursor: pointer; }
.zc-entity-cover :deep(img) { display: block; width: 100%; height: 100%; object-fit: cover; }
.zc-entity-badge { position: absolute; top: var(--zc-space-2); right: var(--zc-space-2); }

.zc-entity-body { display: flex; flex-direction: column; flex: 1;
  padding: var(--zc-space-3) var(--zc-space-4); }
.zc-entity-titlerow { display: flex; align-items: center; gap: var(--zc-space-2); min-width: 0; }
.zc-entity-name { flex: 0 1 auto; min-width: 0; margin: 0; font-size: var(--zc-font-md);
  font-weight: 600; color: var(--zc-text); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.zc-entity-tags { display: inline-flex; align-items: center; gap: var(--zc-space-1);
  flex: none; }
.zc-entity-menu { flex: none; margin-inline-start: auto; display: inline-flex;
  align-items: center; }

.zc-entity-summary { margin: var(--zc-space-2) 0; font-size: var(--zc-font-sm);
  color: var(--zc-text-sub); line-height: 1.6; display: -webkit-box;
  -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  /* 空简介也占满两行高度，否则同排卡片的信息线会错位 */
  min-height: calc(var(--zc-font-sm) * 3.2); }

.zc-entity-meta { margin-top: auto; padding-top: var(--zc-space-2);
  font-size: var(--zc-font-xs); color: var(--zc-text-weak); }
.zc-entity-actions { display: flex; align-items: center; flex-wrap: wrap;
  gap: var(--zc-space-2); margin-top: var(--zc-space-3); }
.zc-entity-footer { display: flex; align-items: center; justify-content: space-between;
  gap: var(--zc-space-2); margin-top: var(--zc-space-3); padding-top: var(--zc-space-3);
  border-top: 1px solid var(--zc-border-soft); }

@media (prefers-reduced-motion: reduce) {
  .zc-entity-card, .zc-entity-card:hover { transition: none; transform: none; }
}
</style>
