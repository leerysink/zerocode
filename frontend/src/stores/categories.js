// 商店分类字典（ADR-0061 §4）：全站唯一的分类读数口。
// 以前这里是 utils/storeCategories.js 里写死的 5 个中文串，与后端 app/store/release.py
// 的常量「同源镜像」——靠注释维持同源，改一边忘另一边就漂移。现在真源在 store_category 表，
// 本 store 只缓存一次 GET /store/categories 的结果，各页从 enabled/all 取值。
import { defineStore } from 'pinia'
import { storeCategories } from '../api'
import { CATEGORY_SEED, DEFAULT_STORE_CATEGORY } from '../utils/storeCategories'

// 字典没回来之前先垫种子值：上架与编辑的下拉不该因为一次网络往返而空着
const seedItems = () => CATEGORY_SEED.map((k, i) => ({
  key: k, label: k, sort: (i + 1) * 10, enabled: true,
}))

export const useCategoryStore = defineStore('storeCategories', {
  state: () => ({ items: seedItems(), loaded: false }),
  getters: {
    /** 可选面：上架与运营编辑只给启用项 */
    enabled: (s) => s.items.filter((i) => i.enabled),
    /** 可选项的 key 列表（分类胶囊、下拉都用它） */
    enabledKeys() { return this.enabled.map((i) => i.key) },
    /** 展示名：停用分类的存量条目仍要显示它的 label，所以查全量而不是只查启用项 */
    labelOf: (s) => (key) => {
      const hit = s.items.find((i) => i.key === key)
      return hit ? hit.label : (key || DEFAULT_STORE_CATEGORY)
    },
  },
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return
      try {
        this.items = (await storeCategories()).data
        this.loaded = true
      } catch {
        // 拉不到就留着上一次（或种子）的读数：分类是运营细节，不该把整页拖成错误态
        this.loaded = false
      }
    },
  },
})
