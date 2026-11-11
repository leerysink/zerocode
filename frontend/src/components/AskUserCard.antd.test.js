// @vitest-environment happy-dom
// 单选组的真渲染回归：label 同时是 radio 的 value，模型漏 label 时所有 value 都是
// undefined，与「未选」的组值相等 → AntD 把每个选项都画成已选、点谁都不变
// （2026-11-11 实跑撞到）。其它测试都 stub 了 antd，这层交互只有真组件能验；
// 归一化本身在 messageModel.optionsOf / 后端 _option_of。
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Antd from 'ant-design-vue'

vi.mock('../api', () => ({
  getMessages: vi.fn(), getSessionDetail: vi.fn(), listModels: vi.fn(),
  listWorkspaces: vi.fn(), patchSessionModel: vi.fn(), wsUrl: vi.fn(() => 'ws://t'),
}))

import AskUserCard from './AskUserCard.vue'
import { askQuestionsOf } from '../stores/messageModel'

function mountCard(question) {
  setActivePinia(createPinia())
  return mount(AskUserCard, {
    props: { questions: [question] },
    global: { plugins: [Antd] },
    attachTo: document.body,
  })
}

describe('AskUserCard 单选组（真 antd）', () => {
  it('缺 label 的选项归一化后：默认无选中，点哪项只选中哪项', async () => {
    const q = askQuestionsOf({
      question: '选哪个',
      options: [{ description: '甲' }, { description: '乙' }],
    })[0]
    const w = mountCard(q)
    expect(w.findAll('.ant-radio-wrapper-checked').length).toBe(0)
    await w.findAll('.zc-opt')[1].trigger('click')
    const checked = w.findAll('.ant-radio-wrapper-checked')
    expect(checked.length).toBe(1)
    expect(checked[0].text()).toContain('乙')
    w.unmount()
  })
})
