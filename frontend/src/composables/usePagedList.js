// 控制台列表分页：全量数组留在手里，只切眼下这一页。
// 后端不分页是刻意的——页头统计、tab 计数都要全量口径才对得上（见 ADR-0071）。
// 每页条数是「本机浏览器的现场」，与侧栏宽度同一策略，所以只落 localStorage。
import { computed, reactive, ref } from 'vue'

export const DEFAULT_PAGE_SIZE = 6
export const PAGE_SIZES = [6, 12, 24]
export const PAGE_SIZE_STORAGE_KEY = 'zc.console.page-size.v1'

function browserStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

// 白名单外一律不认：手改 localStorage 或以后改档位，都不该换来一个算不出页数的 pageSize
function savedPageSize(storage) {
  const n = Number(storage?.getItem(PAGE_SIZE_STORAGE_KEY))
  return PAGE_SIZES.includes(n) ? n : DEFAULT_PAGE_SIZE
}

export function usePagedList(source, options = {}) {
  const storage = options.storage ?? browserStorage()
  const pageSize = ref(savedPageSize(storage))
  const page = ref(1)
  const list = computed(() => source.value || [])
  const total = computed(() => list.value.length)
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
  // 夹在 pageCount 上是派生出来的，不是回写 page：5s 轮询把列表抽短时，
  // 用户看到的是最后一页而不是空页，等他改过条数或手动翻页才会真正挪动 page。
  const current = computed(() => Math.min(page.value, pageCount.value))
  const paged = computed(() => {
    const from = (current.value - 1) * pageSize.value
    return list.value.slice(from, from + pageSize.value)
  })

  const goTo = (n) => { page.value = n }
  const resetPage = () => { page.value = 1 }

  function setPageSize(n) {
    pageSize.value = Number(n) // antd 的档位是字符串，统一收口成数字再算页数
    page.value = 1 // 换档后原来的页码没有意义：第 3 页 ≠ 第 3 页了，从头看最不容易迷路
    try {
      storage?.setItem(PAGE_SIZE_STORAGE_KEY, String(pageSize.value))
    } catch {
      // 隐私模式/配额满：这次记住了下次不记得，翻页本身照常用
    }
  }

  const showTotal = (t) => `共 ${t} 条`
  // 表格列表（我的申请）用 a-table 自带的那条分页，和 ZcPagination 走同一个状态源
  const tablePagination = computed(() => ({
    current: current.value,
    pageSize: pageSize.value,
    total: total.value,
    showSizeChanger: true,
    pageSizeOptions: PAGE_SIZES.map(String),
    showTotal,
    onChange: (n, size) => (Number(size) === pageSize.value ? goTo(n) : setPageSize(size)),
  }))

  // 返回 reactive 而不是散装 ref：调用点拿一个 pager 对象就能在模板里写 pager.paged，
  // 六个列表若各自解构五六个 ref 进 setup，名字会淹掉页面。ZcPagination 也收这一个对象。
  return reactive({
    paged, total, pageCount, current, pageSize,
    goTo, resetPage, setPageSize, showTotal, tablePagination,
  })
}
