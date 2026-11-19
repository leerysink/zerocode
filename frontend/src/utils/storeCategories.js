// 商店分类的兜底种子与代码层默认值（ADR-0061 §4）。
// 分类的真源已经是后端 store_category 字典表，运行期经 stores/categories.js 拉取；
// 这份 SEED 只在「字典还没回来 / 后端不可达」时垫底，让下拉框不至于是空的——
// 它刻意不再被当作可选面使用，所以名字不叫 CATEGORIES。
export const DEFAULT_STORE_CATEGORY = '其他'
export const CATEGORY_SEED = ['效率工具', '数据管理', '内容展示', '演示样例', '其他']
