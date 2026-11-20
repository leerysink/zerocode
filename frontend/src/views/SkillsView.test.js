// @vitest-environment happy-dom
// SkillsView 两态卡片：输入态 → 草稿态 → 安装成功回输入态。
// 桩件方案对齐 AppsView.test.js（vi.mock antd，不挂真 Antd）：
// a-card 必须声明 title prop 才会把标题渲染成文本，a-textarea 必须回抛 update:value 才接得上 v-model。
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  api: {
    listSkills: vi.fn(() => Promise.resolve({ data: [] })),
    installSkill: vi.fn(),
    previewSkill: vi.fn(),
    patchSkill: vi.fn(),
    deleteSkill: vi.fn(),
    generateSkill: vi.fn(),
    adoptSkill: vi.fn(),
    skillFiles: vi.fn(() => Promise.resolve({ data: { files: [] } })),
    skillFileContent: vi.fn(() => Promise.resolve({ data: { content: '' } })),
    skillFileRawUrl: vi.fn((id, p) => `/api/skills/${id}/files/raw?path=${p}`),
    previewFileContent: vi.fn(() => Promise.resolve({ data: { content: '' } })),
    previewFileRawUrl: vi.fn((t, p) => `/api/skills/preview/${t}/raw?path=${p}`),
  },
  antd: {
    message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
    Modal: { confirm: vi.fn() },
  },
  // SkillsView 顶层调 useRouter()：本测试不建真 router，给个桩避免注入告警（对齐 StoreView.test.js）
  router: { push: vi.fn(), back: vi.fn() },
}))
vi.mock('../api', () => mocks.api)
vi.mock('ant-design-vue', () => mocks.antd)
vi.mock('vue-router', () => ({ useRouter: () => mocks.router }))

import SkillsView from './SkillsView.vue'

const DRAFT = {
  name: 'meeting-minutes',
  description: '把会议记录整理成结构化纪要',
  content: '---\nname: meeting-minutes\ndescription: 周报\n---\n正文',
  warnings: [],
}

const slot = (name) => ({
  name, props: ['disabled', 'title', 'type', 'size', 'loading'],
  render() {
    const attrs = { class: `stub-${name}` }
    if (this.disabled) attrs.disabled = ''
    return h(name === 'a-button' ? 'button' : 'div', attrs, this.$slots.default?.())
  },
})
const STUBS = {
  // title 走属性写法（<a-card title="...">），故必须声明成 prop 再渲染，否则 text() 找不到卡片标题
  'a-card': { name: 'a-card', props: ['title'], render() {
    return h('div', { class: 'stub-card' }, [
      h('div', { class: 'stub-card-title' }, this.title ?? ''),
      this.$slots.title?.(), this.$slots.extra?.(), this.$slots.default?.()]) } },
  'a-button': slot('a-button'),
  'a-input': { name: 'a-input', props: ['value'], render() {
    return h('input', { class: 'stub-input', value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
  'a-textarea': { name: 'a-textarea', props: ['value'], render() {
    return h('textarea', { class: 'stub-textarea', value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value) }) } },
  'a-alert': { name: 'a-alert', props: ['type'], render() {
    return h('div', { class: 'stub-alert' }, [this.$slots.message?.()]) } },
  'a-table': {
    name: 'a-table', props: ['columns', 'dataSource', 'loading', 'rowKey'],
    render() {
      const bodySlot = this.$slots.bodyCell
      const rows = (this.dataSource || []).map((rec) =>
        h('div', { class: 'stub-table-row', key: rec.id },
          (this.columns || []).map((col) =>
            h('span', { class: 'stub-table-cell', 'data-key': col.key || col.dataIndex },
              bodySlot ? bodySlot({ column: col, record: rec, text: rec[col.dataIndex] })
                : String(rec[col.dataIndex] ?? '')))))
      return h('div', { class: 'stub-table' }, rows)
    },
  },
  'a-switch': slot('a-switch'),
  'a-empty': slot('a-empty'),
  'SkillDetailDrawer': {
    name: 'SkillDetailDrawer',
    props: { open: Boolean, title: String, files: Array, skillMd: String, confirmable: Boolean, confirming: Boolean },
    emits: ['close', 'confirm'],
    render() { return h('div', { class: 'stub-skill-drawer' }) },
  },
}

async function mountView() {
  const wrapper = mount(SkillsView, { global: { stubs: STUBS } })
  await flushPromises()
  return wrapper
}

// AI 卡片：按标题文本定位，不依赖卡片顺序（安装卡/候选卡/表格都可能是第 N 张）
const aiCard = (wrapper) =>
  wrapper.findAll('.stub-card').find((c) => c.text().includes('用 AI 生成'))
// 按钮文案归一化去空白，避免 '生成' 误匹配 '重新生成'
const aiBtn = (wrapper, label) =>
  aiCard(wrapper).findAll('.stub-a-button')
    .find((b) => b.text().replace(/\s+/g, '') === label)
const promptBox = (wrapper) => wrapper.findAll('.stub-textarea')[0]

beforeEach(() => {
  vi.clearAllMocks()
  mocks.api.listSkills.mockResolvedValue({ data: [] })
})

describe('SkillsView AI 生成卡片', () => {
  it('初始为输入态：描述输入框 + 生成按钮，无草稿区', async () => {
    const wrapper = await mountView()
    expect(aiCard(wrapper)).toBeTruthy()
    expect(promptBox(wrapper).exists()).toBe(true)
    expect(aiBtn(wrapper, '生成').exists()).toBe(true)
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(false)
  })

  it('空描述点生成 → warning，不发请求', async () => {
    const wrapper = await mountView()
    await aiBtn(wrapper, '生成').trigger('click')
    expect(mocks.antd.message.warning).toHaveBeenCalled()
    expect(mocks.api.generateSkill).not.toHaveBeenCalled()
  })

  it('纯空格描述点生成 → 同样不发请求（trim 边界）', async () => {
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('   \n\t ')
    await aiBtn(wrapper, '生成').trigger('click')
    expect(mocks.antd.message.warning).toHaveBeenCalled()
    expect(mocks.api.generateSkill).not.toHaveBeenCalled()
  })

  it('生成成功 → 切草稿态：warnings 标黄 + 摘要 + 可编辑正文 + 三按钮', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: { ...DRAFT, warnings: ['name 含非 ASCII'] } })
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('帮我整理会议纪要')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    expect(mocks.api.generateSkill).toHaveBeenCalledWith({ prompt: '帮我整理会议纪要' })
    const card = aiCard(wrapper).text()
    expect(card).toContain('meeting-minutes')
    expect(card).toContain('name 含非 ASCII')
    expect(aiCard(wrapper).find('.stub-alert').exists()).toBe(true)
    expect(aiBtn(wrapper, '安装').exists()).toBe(true)
    expect(aiBtn(wrapper, '重新生成').exists()).toBe(true)
    expect(aiBtn(wrapper, '取消').exists()).toBe(true)
    // 正文可编辑区里就是草稿原文
    expect(wrapper.find('.zc-ai-draft').element.value).toBe(DRAFT.content)
  })

  it('warnings 为空 → 不渲染 alert', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: DRAFT })
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    expect(aiCard(wrapper).find('.stub-alert').exists()).toBe(false)
  })

  it('生成在飞时连点两次只发一次请求（generating 防重入）', async () => {
    let resolveGen
    mocks.api.generateSkill.mockReturnValue(new Promise((r) => { resolveGen = r }))
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await aiBtn(wrapper, '生成').trigger('click')
    expect(mocks.api.generateSkill).toHaveBeenCalledTimes(1)
    resolveGen({ data: DRAFT })
    await flushPromises()
  })

  it('安装成功 → 回输入态 + success toast + 重拉列表', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: DRAFT })
    mocks.api.adoptSkill.mockResolvedValue({ data: { id: 1, name: 'meeting-minutes', source_type: 'ai' } })
    const wrapper = await mountView()
    const callsAfterLoad = mocks.api.listSkills.mock.calls.length
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    await aiBtn(wrapper, '安装').trigger('click')
    await flushPromises()
    expect(mocks.api.adoptSkill).toHaveBeenCalledTimes(1)
    // 安装带上正文与描述原文
    expect(mocks.api.adoptSkill).toHaveBeenCalledWith({ content: DRAFT.content, prompt: '描述' })
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已安装技能：meeting-minutes')
    expect(mocks.api.listSkills.mock.calls.length).toBeGreaterThan(callsAfterLoad)
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(false)
  })

  it('安装在飞时连点两次只发一次请求（adopting 防重入）', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: DRAFT })
    let resolveAdopt
    mocks.api.adoptSkill.mockReturnValue(new Promise((r) => { resolveAdopt = r }))
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    await aiBtn(wrapper, '安装').trigger('click')
    await aiBtn(wrapper, '安装').trigger('click')
    expect(mocks.api.adoptSkill).toHaveBeenCalledTimes(1)
    resolveAdopt({ data: { id: 1, name: 'meeting-minutes' } })
    await flushPromises()
  })

  it('安装撞 409 → 提示 slug 冲突，草稿态保留', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: DRAFT })
    mocks.api.adoptSkill.mockRejectedValue({ response: { status: 409 } })
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    await aiBtn(wrapper, '安装').trigger('click')
    await flushPromises()
    expect(mocks.antd.message.error).toHaveBeenCalledWith(expect.stringContaining('冲突'))
    // 草稿没丢，用户可改完再装
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(true)
  })

  it('安装其他失败 → 透出服务端 detail', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: DRAFT })
    mocks.api.adoptSkill.mockRejectedValue({ response: { status: 400, data: { detail: '文件超过 1MB 上限: SKILL.md' } } })
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    await aiBtn(wrapper, '安装').trigger('click')
    await flushPromises()
    expect(mocks.antd.message.error).toHaveBeenCalledWith('文件超过 1MB 上限: SKILL.md')
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(true)
  })

  it('取消 → 回输入态且保留描述文本', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: DRAFT })
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('我的描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    await aiBtn(wrapper, '取消').trigger('click')
    await flushPromises()
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(false)
    expect(promptBox(wrapper).element.value).toBe('我的描述')
  })

  it('生成失败 → 回输入态 + message.error 显示 detail', async () => {
    mocks.api.generateSkill.mockRejectedValue({ response: { data: { detail: '未配置模型，请先到设置页配置' } } })
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    expect(mocks.antd.message.error).toHaveBeenCalledWith('未配置模型，请先到设置页配置')
    expect(wrapper.find('.zc-ai-draft').exists()).toBe(false)
  })

  it('生成失败且无 detail → 兜底文案，不显示 undefined', async () => {
    mocks.api.generateSkill.mockRejectedValue(new Error('network'))
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    expect(mocks.antd.message.error).toHaveBeenCalledWith('生成失败，请重试')
  })

  it('草稿未改动点重新生成 → 直接再生成，不弹确认', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: DRAFT })
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    await aiBtn(wrapper, '重新生成').trigger('click')
    await flushPromises()
    expect(mocks.antd.Modal.confirm).not.toHaveBeenCalled()
    expect(mocks.api.generateSkill).toHaveBeenCalledTimes(2)
  })

  it('草稿被改过点重新生成 → 弹确认覆盖', async () => {
    mocks.api.generateSkill.mockResolvedValue({ data: DRAFT })
    const wrapper = await mountView()
    await promptBox(wrapper).setValue('描述')
    await aiBtn(wrapper, '生成').trigger('click')
    await flushPromises()
    await wrapper.find('.zc-ai-draft').setValue('手改过的正文')
    await aiBtn(wrapper, '重新生成').trigger('click')
    expect(mocks.antd.Modal.confirm).toHaveBeenCalledTimes(1)
    // 未确认前不会二次请求
    expect(mocks.api.generateSkill).toHaveBeenCalledTimes(1)
  })

  it('「来源」列走标签函数：ai→AI 生成、github→GitHub、未知值原样', async () => {
    mocks.api.listSkills.mockResolvedValue({
      data: [
        { id: 1, name: 'a', description: 'd', source_type: 'ai', enabled: true },
        { id: 2, name: 'b', description: 'd', source_type: 'github', enabled: true },
        { id: 3, name: 'c', description: 'd', source_type: 'future-type', enabled: true },
      ] })
    const wrapper = await mountView()
    const cells = wrapper.findAll('[data-key="source"]').map((c) => c.text())
    expect(cells).toEqual(['AI 生成', 'GitHub', 'future-type'])
  })
})

describe('SkillsView 返回', () => {
  it('返回固定回对话页：四页同一条语义，不再按浏览器历史分叉', async () => {
    window.history.replaceState(null, '')
    const wrapper = await mountView()
    await wrapper.find('.zc-back').trigger('click')
    expect(mocks.router.push).toHaveBeenCalledWith('/')
    expect(mocks.router.back).not.toHaveBeenCalled()
  })
})

describe('SkillsView 整页层级统一（页头走薄件 + 清单住进卡片）', () => {
  const SKILLS = [
    { id: 1, name: 'pdf', description: 'd', source_type: 'github', enabled: true,
      file_count: 1, total_bytes: 10, has_scripts: false },
    { id: 2, name: 'docx', description: 'd', source_type: 'url', enabled: true,
      file_count: 1, total_bytes: 10, has_scripts: false },
  ]

  it('页头来自 ZcPageHeader：不再自绘一份页头 markup', async () => {
    mocks.api.listSkills.mockResolvedValue({ data: [] })
    const w = await mountView()
    expect(w.find('[data-test="page-header"]').exists()).toBe(true)
    expect(w.find('[data-test="page-title"]').text()).toBe('技能')
  })

  it('技能清单住在一枚带标题的卡里，标题右侧给计数徽标', async () => {
    mocks.api.listSkills.mockResolvedValue({ data: SKILLS })
    const w = await mountView()
    const card = w.find('[data-test="skill-list-card"]')
    expect(card.exists(), '清单必须在卡片壳里，不能继续是裸表格').toBe(true)
    expect(card.text()).toContain('技能清单')
    expect(card.find('[data-test="skill-count"]').text()).toBe('2')
    expect(card.find('.stub-table').exists()).toBe(true)
  })
})

describe('SkillsView 多文件展示（ADR-0061）', () => {
  const installCard = (w) => w.findAll('.stub-card').find((c) => c.text().includes('从链接安装'))
  const previewBtn = (w) => installCard(w).findAll('.stub-a-button')
    .find((b) => b.text().replace(/\s+/g, '') === '预览')
  const drawers = (w) => w.findAllComponents({ name: 'SkillDetailDrawer' })

  it('列表名称下渲染副信息「N 文件 · X KB · 含脚本」', async () => {
    mocks.api.listSkills.mockResolvedValue({ data: [
      { id: 1, name: 'pdf', description: 'd', source_type: 'github', enabled: true,
        file_count: 3, total_bytes: 2048, has_scripts: true },
      { id: 2, name: 'note', description: 'd', source_type: 'url', enabled: true,
        file_count: 1, total_bytes: 100, has_scripts: false },
    ] })
    const w = await mountView()
    const subs = w.findAll('[data-test="skill-subline"]').map((s) => s.text())
    expect(subs[0]).toBe('3 文件 · 2.0 KB · 含脚本')
    expect(subs[1]).toBe('1 文件 · 100 B')
  })

  it('点详情 → 开只读抽屉并拉文件清单', async () => {
    mocks.api.listSkills.mockResolvedValue({ data: [
      { id: 7, name: 'pdf', description: 'd', source_type: 'github', enabled: true,
        file_count: 1, total_bytes: 10, has_scripts: false }] })
    mocks.api.skillFiles.mockResolvedValue({ data: { files: [{ path: 'SKILL.md', size: 10, kind: 'text' }] } })
    const w = await mountView()
    const detailBtn = w.findAll('[data-key="ops"] .stub-a-button')
      .find((b) => b.text().replace(/\s+/g, '') === '详情')
    await detailBtn.trigger('click')
    await flushPromises()
    expect(mocks.api.skillFiles).toHaveBeenCalledWith(7)
    const d = drawers(w)[0]
    expect(d.props('open')).toBe(true)
    expect(d.props('files')).toEqual([{ path: 'SKILL.md', size: 10, kind: 'text' }])
    expect(d.props('confirmable')).toBe(false)
  })

  it('预览→确认安装走 token 快照（preview==install）', async () => {
    mocks.api.previewSkill.mockResolvedValue({ data: {
      token: 'tok123', name: 'pdf', description: 'd',
      files: [{ path: 'SKILL.md', size: 10, kind: 'text' }], skill_md: '正文' } })
    mocks.api.installSkill.mockResolvedValue({ data: { id: 9, name: 'pdf', source_type: 'github' } })
    const w = await mountView()
    await w.find('.stub-input').setValue('https://github.com/o/r')
    await previewBtn(w).trigger('click')
    await flushPromises()
    expect(mocks.api.previewSkill).toHaveBeenCalledWith({ url: 'https://github.com/o/r' })
    const p = drawers(w)[1]
    expect(p.props('open')).toBe(true)
    expect(p.props('confirmable')).toBe(true)
    expect(p.props('skillMd')).toBe('正文')
    p.vm.$emit('confirm')
    await flushPromises()
    expect(mocks.api.installSkill).toHaveBeenCalledWith({ token: 'tok123' })
    expect(mocks.antd.message.success).toHaveBeenCalledWith('已安装技能：pdf')
  })

  it('预览多命中 → 候选卡，带 subpath 二次预览', async () => {
    mocks.api.previewSkill
      .mockResolvedValueOnce({ data: { candidates: [{ path: 'skills/a' }] } })
      .mockResolvedValueOnce({ data: { token: 't2', name: 'a', description: 'd', files: [], skill_md: null } })
    const w = await mountView()
    await w.find('.stub-input').setValue('https://github.com/o/r')
    await previewBtn(w).trigger('click')
    await flushPromises()
    expect(w.text()).toContain('选择要预览的技能')
    await w.find('.zc-cand-row .stub-a-button').trigger('click')
    await flushPromises()
    expect(mocks.api.previewSkill).toHaveBeenLastCalledWith({ url: 'https://github.com/o/r', subpath: 'skills/a' })
    expect(drawers(w)[1].props('open')).toBe(true)
  })
})
