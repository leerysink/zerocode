<script setup>
// browser_open / browser_step / browser_close 的步骤卡（规格 2026-11-02 §9、US-22 / US-23）。
// 分工：PageInspectCard 是「看一眼」，这张是「走一步」——重点是断言结论、本步增量错误，
// 以及弹窗有没有被吞掉（US-11：本项目最阴险的失效——confirm 被自动取消，删除根本没发生）。
//
// 渲染契约（Task 5 定下的最终形状，别再按「有没有 error 键」猜）：
//   · report / step 结果恒有 seq(仅 report) / action / target / ok / passed。
//   · `ok === false` → 「点不动」（动作层失败），另有 `error`，`passed` 恒 null。
//   · `ok === true && passed === false` → 「走了但结果不对」（业务断言没过），另有 `note`。
//   · `passed` 是 `null | bool`：没给 expect 也是 null，不假装判过。
// 这两件事在本项目里从头到尾都是分开的账（`steps_failed` 只数后者、`steps_action_failed` 只数前者），
// 所以卡片上既不同色也不同文案——糊成一个红就等于把这条不变量在最后一米抹掉。
// 两种失败形状都可能带 `screenshot`（裁定 R-26），存在就有 url / width / height / bytes。
// `session_id` 不出现在任何返回值里（ADR-0037），这里也就无从显示它。
//
// 刻意不读 `msg.args`：fill 的 value 只有指向 sensitive 元素的那一份才被遮断（裁定 R-33），
// 非敏感目标的 value 按设计仍是明文（备注、地址这类要能被回看）；卡片再照 args 抄一遍
// 就把这条边界抹平了，而 ref 与元素名都在结果里带着，不需要回退到参数。
import { computed, ref } from 'vue'

const props = defineProps({
  msg: { type: Object, required: true },
  progress: { type: Object, default: null },
})

const RUNNING_TITLE = {
  browser_open: '正在打开浏览器会话…',
  browser_step: '正在执行浏览器步骤…',
  browser_close: '正在收口浏览器会话…',
}
// 动作名说人话：徽标与文案要一眼看出「这一步对谁做了什么」，而不必记住 click/fill 的枚举。
// `open` 只有收口报告的行里会出现（browser_open 的结果不带 action 回显）。
const ACTION_TEXT = {
  open: '打开会话', click: '点击', fill: '填入', press: '按键', select: '选择',
  scroll_into_view: '滚动到可见', navigate: '跳转',
}
const EXPECT_TEXT = {
  text_contains: (v) => `页面应出现「${v}」`,
  element_visible: (v) => `元素应可见：${v}`,
  url_matches: (v) => `地址应匹配 ${v}`,
  no_new_console_error: () => '本步不应有新控制台报错',
}
const DIALOG_TEXT = {
  confirm: '确认框', alert: '提示框', prompt: '输入框', beforeunload: '离开页面确认框',
}
// 清单折叠阈值：超过这么多条就先露一屏，剩下的点开再看（40 条是后端默认预算，全摊会把卡片淹了）
const LIST_FOLD = 8

const result = computed(() => props.msg.result ?? props.msg.tool_result ?? null)
const name = computed(() => props.msg.name ?? props.msg.tool_name ?? 'browser_step')
const actionFailed = computed(() => result.value?.ok === false)
const expectFailed = computed(() => result.value?.ok === true && result.value?.passed === false)
const expectPassed = computed(() => result.value?.ok === true && result.value?.passed === true)

const tone = computed(() => {
  const r = result.value
  if (!r) return 'info'
  if (r.ok === false) return r.status && r.status !== 'running' ? 'muted' : 'error'
  if (r.passed === false) return 'warn'
  if (name.value === 'browser_close') {
    return (closeFailed.value || closeActionFailureShown.value) ? 'warn' : 'ok'
  }
  if (r.timed_out || issues.value) return 'warn'
  return 'ok'
})

const title = computed(() => {
  const r = result.value
  if (!r) return RUNNING_TITLE[name.value] || '正在操作浏览器…'
  if (name.value === 'browser_close') return '本次走查收口'
  if (r.ok === false) {
    if (r.session_gone) return '浏览器会话已结束'
    if (r.status && r.status !== 'running') return '预览未就绪'
    return name.value === 'browser_open' ? '浏览器会话没打开' : '这一步点不动'
  }
  if (r.passed === false) return '走通了，但断言没过'
  if (name.value === 'browser_open') return '浏览器会话已打开'
  return r.passed === true ? '这一步走完了，断言也过了' : '这一步走完了'
})

// 徽标：两种失败各一个说法与一个颜色（`is-action` 红 / `is-expect` 琥珀），
// 共用 `data-test="badge-fail"` 让「这一步有没有坏」一次问清。
const badge = computed(() => {
  const r = result.value
  if (!r) return null
  if (r.ok === false) return { cls: 'is-action', text: '动作失败' }
  if (r.passed === false) return { cls: 'is-expect', text: '断言没过' }
  if (name.value === 'browser_close' && closeActionFailed.value) {
    return { cls: 'is-action', text: `点不动 ${closeActionFailed.value} 步` }
  }
  if (name.value === 'browser_close' && closeFailed.value) {
    return { cls: 'is-expect', text: `断言没过 ${closeFailed.value} 步` }
  }
  return null
})
const passBadge = computed(() => (expectPassed.value ? '断言过了' : ''))

const url = computed(() => (result.value?.ok ? result.value?.url || '' : ''))
const pageTitle = computed(() => (result.value?.ok ? result.value?.title || '' : ''))
const action = computed(() => {
  const a = result.value?.action
  return typeof a === 'string' ? a : ''
})
// target 只有 ref 或 path 两种形态（spec §5.1：navigate 时 target 改填 path）。
// 元素名读后端 echo 的 `target_name`（裁定 R-32，US-22）——它取的是**动作之前**那份清单。
// 刻意不拿本步结果里的 interactive 反查：那是**动作之后**的新快照，而 ref 是每次扫描重生成的一批
// 随机令牌，旧句柄在里面必然查不到（规格 §6.2），用它标「这一步动的谁」只会画出空目标。
const target = computed(() => {
  const t = result.value?.target
  return typeof t === 'string' ? t : ''
})
const targetName = computed(() => {
  const n = result.value?.target_name
  return typeof n === 'string' ? n.trim() : ''
})
// 句柄与 path 怎么分：**只看开头，不看句柄长什么样**。令牌形状归 sidecar 的
// `browser/session_core.py::REF_RE` 管，这里再抄一份形状正则就是第三处判据——漏改的一侧
// 不会红任何测试，症状是历史行安静地退回裸打印。口径与后端 `is_sensitive_ref_or_unknown`
// 一致：`/` 开头的是 navigate 的路径，其余一律当句柄。
const isNavigatePath = (t) => t.startsWith('/')
const handleLabel = (t) => (isNavigatePath(t) ? t : `句柄 ${t}`)
const stepLabel = computed(() => {
  if (!result.value || (!action.value && !target.value)) return ''
  const text = ACTION_TEXT[action.value] || action.value
  if (!target.value) return text || '执行动作'
  // 名字查不到就回落句柄：编一个不存在的名比只显示一个令牌更糟
  const who = targetName.value ? `${targetName.value}（句柄 ${target.value}）`
    : handleLabel(target.value)
  return text ? `${text} ${who}` : who
})
const expectLabel = computed(() => {
  const e = result.value?.expect
  if (!e || !e.type) return ''
  const say = EXPECT_TEXT[e.type]
  return say ? say(e.value ?? '') : `${e.type}${e.value ? `：${e.value}` : ''}`
})
// note 只跟着断言失败出现（后端对 passed:true 给的是空串）：有就摊开、不折叠——
// 「这一步为什么不算过」是这张卡最该被看见的一行，藏进折叠块就等于没写。
const note = computed(() => (expectFailed.value ? result.value?.note || '' : ''))

const dialog = computed(() => result.value?.dialog || null)
const dialogLine = computed(() => {
  const d = dialog.value
  if (!d) return ''
  const kind = DIALOG_TEXT[d.type] || '原生弹窗'
  const how = d.handled === 'accepted' ? '已按确定处理' : '已按取消处理'
  return `页面弹出了${kind}，${how}——这一步要求的操作可能根本没发生`
})

const interactive = computed(() => (result.value?.interactive || []))
const expanded = ref(false)
const shownInteractive = computed(() => {
  const list = interactive.value
  return expanded.value ? list : list.slice(0, LIST_FOLD)
})
// 说的是「什么时候的清单」：step 那张是动作之后的新快照，open 那张是会话建立时的首屏。
// 含糊写「最新清单」会让人以为上一张卡上的句柄还活着——那正是 US-09 那次失败的形状。
const interactiveCaption = computed(() => (name.value === 'browser_open'
  ? '会话建立时的首屏清单'
  : '这一步之后的新清单，旧句柄全部作废'))

const consoleItems = computed(() => result.value?.console_new || [])
const pageErrors = computed(() => result.value?.page_errors_new || [])
const failedRequests = computed(() => result.value?.failed_requests_new || [])
const issues = computed(() => consoleItems.value.length + pageErrors.value.length
  + failedRequests.value.length)

const closeFailed = computed(() => Number(result.value?.steps_failed || 0))
// M-3：缺字段与 0 是两件事。`steps_action_failed` 是裁定 R-31 之后才有的，Task 5/6 期间产生的
// 历史行没有它——那种行照 `|| 0` 读会写成「点不动 0」，而同一张卡的 report 里就摆着红色的
// `ok:false` 行，两张嘴说反话。照本组件对 `steps_used` 的 `!== undefined` 守卫写：缺字段就不画。
const closeActionFailed = computed(() => (result.value?.steps_action_failed === undefined
  ? null : Number(result.value.steps_action_failed || 0)))
const report = computed(() => (name.value === 'browser_close' ? result.value?.report || [] : []))
// M-3 同族的另一张嘴：tone。缺 `steps_action_failed` 的旧历史行里 closeActionFailed 是 null，
// 而 null 是 falsy，`tone` 那行就把整卡判成 'ok'（绿色），同一张卡正文里却摆着红色的 `ok:false`
// report 行——文案上一轮收了，卡片头还在说「一切正常」。缺字段时回落到 report 本身：
// 那份账前端手上就有，不必等后端补字段。字段在时照旧走字段（不重复判定，也不把真 0 判成坏）。
const closeActionFailureShown = computed(() => (closeActionFailed.value === null
  ? report.value.some((item) => item?.ok === false)
  : closeActionFailed.value > 0))
const hint = computed(() => result.value?.hint || '')
const quotaLine = computed(() => {
  const r = result.value
  if (!r) return ''
  const parts = []
  // -1 是「不限」（后端拿它表达额度未设），画成「还剩 -1 步」就是假话
  if (typeof r.steps_left === 'number' && r.steps_left >= 0) parts.push(`本 run 还剩 ${r.steps_left} 步`)
  if (typeof r.checks_left === 'number' && r.checks_left >= 0) parts.push(`还能开 ${r.checks_left} 次会话`)
  if (r.elapsed_ms) parts.push(`耗时 ${r.elapsed_ms}ms`)
  return parts.join(' · ')
})

const shotBroken = ref(false)
const screenshot = computed(() => (shotBroken.value ? null : result.value?.screenshot || null))
const shotSize = computed(() => {
  const s = screenshot.value
  return s?.width && s?.height ? `${s.width} × ${s.height}` : ''
})

// 边角事实单独成行：遮断过几处、裁没裁、采集超时、补图没成功、上一个会话被自动关掉。
// 这些都不说出口的话，用户会把一份被裁过的部分页面读成完整页面。
const notes = computed(() => {
  const r = result.value
  if (!r) return []
  const out = []
  // `auto_closed_previous` 不在这里重述：后端把同一件事写在 `hint` 上（裁定 R-25），
  // 而 hint 已经单独成行——两处各印一遍就成了同义反复。
  if (r.redacted) out.push(`已遮断 ${r.redacted} 处凭据`)
  if (r.truncated || r.text_truncated || r.interactive_truncated) out.push('内容较长，已按预算截断')
  if (r.timed_out) out.push('等待超时，以下是当时已渲染的内容')
  // 会话已结束 / 额度已用尽那两类「什么都没做成」的步不扣额度（US-12），不说人话就会被读成白挨一次罚款
  if (r.steps_charged === false) out.push('这一步没有扣步数额度')
  if (r.screenshot_note) out.push(r.screenshot_note)
  return out
})

const loadingLine = computed(() => props.progress?.line || RUNNING_TITLE[name.value] || '正在操作浏览器…')

// report 行的三档：点不动 / 断言没过 / 推进了。与卡片同一把尺，不新造判定。
function rowTone(item) {
  if (item.ok === false) return 'is-action'
  if (item.passed === false) return 'is-expect'
  return 'is-done'
}
function rowLabel(item) {
  const a = ACTION_TEXT[item.action] || item.action || '步骤'
  const t = typeof item.target === 'string' && item.target ? item.target : ''
  // M-6：报告的行也不能只留一个作废句柄。按规格 §6.2 那些令牌每扫一次就整批换掉，读报告的人手上
  // 没有任何一份清单能对回它是谁。名字取 report 项的 `target_name`（后端 _step 算好的那一份，
  // 来自动作之前的清单），查不到——包括 Task 5/6 期间没有这个字段的历史行——照旧回落句柄 / path。
  const n = typeof item.target_name === 'string' ? item.target_name.trim() : ''
  if (!t) return a
  const who = n ? `${n}（句柄 ${t}）` : handleLabel(t)
  return `${a} ${who}`
}
function rowVerdict(item) {
  if (item.ok === false) return item.error || '动作未成功'
  if (item.passed === false) return item.note || '断言未通过'
  if (item.passed === true) return '断言过了'
  return '推进了（未声明断言）'
}
</script>

<template>
  <div class="zc-page" :class="`is-${tone}`">
    <div class="zc-page-head">
      <span class="zc-page-title">{{ title }}</span>
      <span class="zc-page-tool">{{ name }}</span>
      <span v-if="badge" class="zc-page-badge" :class="badge.cls" data-test="badge-fail">{{ badge.text }}</span>
      <span v-else-if="passBadge" class="zc-page-badge is-pass" data-test="badge-pass">{{ passBadge }}</span>
      <a v-if="url" class="zc-page-link" :href="url" target="_blank" rel="noopener">打开预览</a>
    </div>

    <div v-if="!result" class="zc-page-loading">{{ loadingLine }}</div>
    <template v-else>
      <div v-if="stepLabel || expectLabel" class="zc-page-step">
        <span>{{ stepLabel }}</span>
        <span v-if="expectLabel" class="zc-page-expect">断言：{{ expectLabel }}</span>
      </div>

      <!-- ① 点不动的原因（红）与 ② 断言没过的那一句（琥珀）：同一种「这一步坏在哪」，不同颜色不同措辞 -->
      <div v-if="actionFailed" class="zc-page-verdict is-action" data-test="verdict">
        {{ result.error || '浏览器操作未成功' }}
      </div>
      <div v-if="note" class="zc-page-verdict is-expect" data-test="verdict">
        {{ note }}
      </div>

      <!-- 弹窗必须显式说出口（US-11）：它被吞掉时页面什么都没改，而动作报的是成功 -->
      <div v-if="dialog" class="zc-page-dialog" data-test="dialog">
        <div>{{ dialogLine }}</div>
        <div v-if="dialog.message" class="zc-page-dialog-msg">弹窗原文：{{ dialog.message }}</div>
      </div>

      <div v-if="pageTitle" class="zc-page-pagetitle">{{ pageTitle }}</div>
      <div v-if="quotaLine" class="zc-page-meta">{{ quotaLine }}</div>

      <div v-if="screenshot" class="zc-page-shot">
        <a-image
          data-test="step-shot"
          :src="screenshot.url"
          :width="320"
          alt="这一步的现场截图"
          @error="shotBroken = true"
        />
        <div v-if="shotSize" class="zc-page-note">{{ shotSize }}</div>
      </div>
      <!-- 图裂降级成提示：`/api/browser/shots/...` 受鉴权，会话清理或换账号后就是 404/403，
           那时这张图是唯一会「静默消失」的证据，必须留下一句话（同 PageInspectCard 的口径） -->
      <div v-else-if="shotBroken" class="zc-page-note" data-test="shot-broken">
        截图已失效（会话已清理或当前账号无权访问），这一步的结论仍然有效
      </div>

      <div v-if="interactive.length" class="zc-page-section" data-test="interactive">
        <div class="zc-page-section-title">
          可交互元素（{{ interactive.length }}）· {{ interactiveCaption }}
        </div>
        <ul class="zc-page-list">
          <li v-for="item in shownInteractive" :key="item.ref" :class="{ 'is-disabled': item.disabled }">
            <span class="zc-page-method">{{ item.ref }}</span>
            <span class="zc-page-status">{{ item.role }}</span>
            <span class="zc-page-url">{{ item.name }}</span>
            <span v-if="item.sensitive" class="zc-page-flag" data-test="sensitive-chip">敏感</span>
            <span v-if="item.disabled" class="zc-page-flag">禁用</span>
          </li>
        </ul>
        <button v-if="interactive.length > LIST_FOLD" class="zc-page-toggle" type="button"
                @click="expanded = !expanded">
          {{ expanded ? '收起清单' : `展开全部 ${interactive.length} 条` }}
        </button>
      </div>

      <div v-if="consoleItems.length" class="zc-page-section">
        <div class="zc-page-section-title">本步新增控制台（{{ consoleItems.length }}）</div>
        <ul class="zc-page-list">
          <li v-for="(item, i) in consoleItems" :key="`c${i}`">
            <span class="zc-page-level">{{ item.level }}</span>{{ item.text }}
          </li>
        </ul>
      </div>

      <div v-if="pageErrors.length" class="zc-page-section">
        <div class="zc-page-section-title">本步未捕获异常（{{ pageErrors.length }}）</div>
        <ul class="zc-page-list">
          <li v-for="(item, i) in pageErrors" :key="`p${i}`" class="is-error">{{ item }}</li>
        </ul>
      </div>

      <div v-if="failedRequests.length" class="zc-page-section">
        <div class="zc-page-section-title">本步新增失败请求（{{ failedRequests.length }}）</div>
        <ul class="zc-page-list">
          <li v-for="(item, i) in failedRequests" :key="`f${i}`">
            <span class="zc-page-method">{{ item.method }}</span>
            <span class="zc-page-status" :class="{ 'is-bad': item.status >= 400 }">
              {{ item.blocked ? 'blocked' : (item.status || 'failed') }}
            </span>
            <span class="zc-page-url">{{ item.url }}</span>
          </li>
        </ul>
      </div>

      <!-- browser_close 的收口报告：两本账分开报数，report 逐行按同一把尺分色 -->
      <div v-if="name === 'browser_close'" class="zc-page-section" data-test="close">
        <div class="zc-page-counts">
          <span>共 {{ result.steps_total ?? 0 }} 步</span>
          <span :class="{ 'is-bad': closeFailed }">断言未过 {{ closeFailed }}</span>
          <span v-if="closeActionFailed !== null" :class="{ 'is-bad': closeActionFailed }">
            点不动 {{ closeActionFailed }}
          </span>
          <span v-if="result.steps_used !== undefined">已用 {{ result.steps_used }} 步</span>
        </div>
        <ul v-if="report.length" class="zc-page-report" data-test="report">
          <li v-for="item in report" :key="`r${item.seq}`" class="zc-page-report-row" :class="rowTone(item)"
              data-test="report-row">
            <span class="zc-page-seq">{{ item.seq }}</span>
            <span class="zc-page-step-name">{{ rowLabel(item) }}</span>
            <span class="zc-page-verdict-text">{{ rowVerdict(item) }}</span>
            <span v-if="item.replaced_sessions" class="zc-page-flag">替换过 {{ item.replaced_sessions }} 个会话</span>
          </li>
        </ul>
      </div>

      <div v-if="hint" class="zc-page-hint" data-test="hint">{{ hint }}</div>
      <div v-for="(text, i) in notes" :key="`n${i}`" class="zc-page-note">{{ text }}</div>
    </template>
  </div>
</template>

<style scoped>
/* 与 PageInspectCard 同一套卡壳与同一批颜色常量：不新增色值，只复用既有 token 与那张卡的中性色。
   （scoped 样式不跨组件共享，所以这份是按同一规格重述，不是另起一套视觉语言。） */
.zc-page {
  width: 100%;
  max-width: 720px;
  border: 1px solid var(--zc-border);
  border-left: 3px solid var(--zc-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: #fbfcfd;
}
.zc-page.is-ok {
  border-left-color: #52a06b;
  background: #f6fbf7;
}
.zc-page.is-warn {
  border-left-color: #d99a3c;
  background: #fdfaf3;
}
.zc-page.is-error {
  border-left-color: #c4636a;
  background: #fdeeee;
}
.zc-page.is-info {
  border-left-color: var(--zc-primary);
  background: #f4f8fd;
}
.zc-page.is-muted {
  border-left-color: #b6bfca;
}
.zc-page-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.zc-page-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--zc-text);
}
.zc-page-tool {
  font-size: 11px;
  color: var(--zc-text-sub);
  background: rgba(91, 122, 157, 0.08);
  border-radius: 4px;
  padding: 1px 6px;
}
.zc-page-badge {
  font-size: 11px;
  color: #a17c5b;
  background: rgba(161, 124, 91, 0.12);
  border-radius: 999px;
  padding: 1px 8px;
}
/* 「点不动」红、「断言没过」琥珀：两本账在徽标这一层也不许同色 */
.zc-page-badge.is-action {
  color: #a05a5a;
  background: rgba(196, 99, 106, 0.14);
}
/* 琥珀这一档必须自己写死，不能只靠 `.zc-page-badge` 的基础色继承：基础色哪天被改成红，
   「两种失败糊成一个红」就会静默通过全部测试（测试比的是 class 名与文案，比不出继承链）。
   色值与基础色同源，取自 PageInspectCard 既有的琥珀，不引入新色相。 */
.zc-page-badge.is-expect {
  color: #a17c5b;
  background: rgba(161, 124, 91, 0.12);
}
.zc-page-badge.is-pass {
  color: #52a06b;
  background: rgba(82, 160, 107, 0.12);
}
.zc-page-link {
  margin-left: auto;
  font-size: 12px;
  color: var(--zc-primary);
  white-space: nowrap;
}
.zc-page-loading,
.zc-page-note,
.zc-page-meta {
  margin-top: 6px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-page-step {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  font-size: 12.5px;
  color: var(--zc-text);
}
.zc-page-expect {
  color: var(--zc-text-sub);
}
.zc-page-verdict {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.zc-page-verdict.is-action {
  color: #a05a5a;
}
.zc-page-verdict.is-expect {
  color: #a17c5b;
}
.zc-page-dialog {
  margin-top: 8px;
  padding: 8px 10px;
  font-size: 12.5px;
  line-height: 1.7;
  color: #a17c5b;
  background: rgba(217, 154, 60, 0.1);
  border-radius: 6px;
}
.zc-page-dialog-msg {
  margin-top: 4px;
  color: var(--zc-text);
  word-break: break-word;
}
.zc-page-pagetitle {
  margin-top: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--zc-text);
}
.zc-page-section {
  margin-top: 10px;
}
.zc-page-section-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--zc-text-sub);
}
.zc-page-list {
  margin: 4px 0 0;
  padding: 6px 10px;
  max-height: 220px;
  overflow: auto;
  list-style: none;
  background: #fff;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  color: var(--zc-text);
}
.zc-page-list li {
  display: flex;
  gap: 6px;
  align-items: baseline;
  white-space: pre-wrap;
  word-break: break-all;
}
.zc-page-list li.is-disabled {
  color: var(--zc-text-sub);
}
/* 未捕获异常整条是「这一步把页面点崩了」的证据，沿用既有那支红，不新增色值 */
.zc-page-list li.is-error {
  color: #a05a5a;
}
.zc-page-level {
  flex: none;
  font-size: 10px;
  text-transform: uppercase;
  color: #a05a5a;
}
.zc-page-method {
  flex: none;
  font-weight: 600;
}
.zc-page-status {
  flex: none;
  color: var(--zc-text-sub);
}
.zc-page-status.is-bad {
  color: #a05a5a;
}
.zc-page-url {
  min-width: 0;
  color: var(--zc-text);
}
.zc-page-flag {
  flex: none;
  font-size: 10px;
  color: #a17c5b;
  background: rgba(161, 124, 91, 0.12);
  border-radius: 4px;
  padding: 0 5px;
}
.zc-page-toggle {
  margin-top: 4px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12px;
  color: var(--zc-primary);
  cursor: pointer;
}
.zc-page-shot {
  margin-top: 10px;
  overflow: hidden;
  border-radius: 8px;
}
.zc-page-shot :deep(.ant-image) {
  max-width: 100%;
}
.zc-page-shot :deep(img) {
  max-width: 100%;
  height: auto;
  display: block;
}
.zc-page-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--zc-text-sub);
}
.zc-page-counts .is-bad {
  color: #a05a5a;
  font-weight: 500;
}
.zc-page-report {
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--zc-border);
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
}
.zc-page-report-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 5px 10px;
  border-bottom: 1px solid var(--zc-border);
  word-break: break-word;
}
.zc-page-report-row:last-child {
  border-bottom: 0;
}
.zc-page-seq {
  flex: none;
  width: 22px;
  color: var(--zc-text-sub);
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Courier New', monospace;
}
.zc-page-step-name {
  flex: none;
  font-weight: 500;
}
.zc-page-verdict-text {
  min-width: 0;
  color: var(--zc-text-sub);
}
/* 逐行分色沿用徽标那一对：点不动红、断言没过琥珀，推进了保持中性 */
.zc-page-report-row.is-action .zc-page-verdict-text {
  color: #a05a5a;
}
.zc-page-report-row.is-expect .zc-page-verdict-text {
  color: #a17c5b;
}
.zc-page-hint {
  margin-top: 8px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--zc-text);
  background: rgba(91, 122, 157, 0.06);
  border-radius: 6px;
  padding: 8px 10px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
