<script setup>
// 分页条薄件：状态一律住在调用点给的 pager（usePagedList 的返回）里，这里只做显隐与接线。
// 「非空即常驻」收在这一处：条目一少就把整条藏掉，等于把切档入口一起藏了——
// 用户选到 24 之后列表不足 24 条，就再也切不回 6（旧商店页的写法）。
import { PAGE_SIZES } from '../../composables/usePagedList'

const props = defineProps({
  pager: { type: Object, required: true },
  // 一条页面上可能有几条分页（应用页的「我的」「共享给我」），给读屏一个归属
  label: { type: String, default: '分页' },
})

const pageSizeOptions = PAGE_SIZES.map(String)
</script>

<template>
  <nav v-if="props.pager.total" class="zc-pager" data-test="zc-pager" :aria-label="props.label">
    <a-pagination :current="props.pager.current" :total="props.pager.total"
                  :page-size="props.pager.pageSize" :page-size-options="pageSizeOptions"
                  :show-size-changer="true" :show-total="props.pager.showTotal"
                  @update:current="props.pager.goTo" @update:page-size="props.pager.setPageSize" />
  </nav>
</template>

<style scoped>
/* 与旧 .zc-store-pager 同一处理：贴在列表下方靠右，不抢卡片的视线 */
.zc-pager { margin-top: var(--zc-space-3); display: flex; justify-content: flex-end; }
</style>
