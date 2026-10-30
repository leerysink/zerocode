// @vitest-environment happy-dom
// 提问卡（单题）：有选项也常驻人工回答输入框，且与选项互斥；
// 「自行决定」上行固定答复把决定权交还模型，不是取消执行。
import { describe, it, expect, vi } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api', () => ({
  getMessages: vi.fn(), getSessionDetail: vi.fn(), listModels: vi.fn(),
  listWorkspaces: vi.fn(), patchSessionModel: vi.fn(), wsUrl: vi.fn(() => 'ws://t'),
}))

import AskUserCard from './AskUserCard.vue'
import { useChatStore } from '../stores/chat'

const RadioGroupStub = {
  name: 'a-radio-group',
  props: ['value'],
  render() { return h('div', { class: 'stub-radio-group' }, this.$slots.default?.()) },
}
const RadioStub = {
  name: 'a-radio',
  props: ['value'],
  render() { return h('label', { class: 'stub-radio' }, this.$slots.default?.()) },
}
const CheckboxGroupStub = {
  name: 'a-checkbox-group',
  props: ['value'],
  emits: ['update:value'],
  provide() {
    return {
      toggle: (label) => {
        const next = [...(this.value || [])]
        const i = next.indexOf(label)
        if (i >= 0) next.splice(i, 1)
        else next.push(label)
        this.$emit('update:value', next)
      },
    }
  },
  render() { return h('div', { class: 'stub-checkbox-group' }, this.$slots.default?.()) },
}
const CheckboxStub = {
  name: 'a-checkbox',
  props: ['value'],
  inject: ['toggle'],
  render() {
    return h('label', {
      class: 'stub-checkbox',
      onClick: () => this.toggle(this.value),
    }, this.$slots.default?.())
  },
}
const TextareaStub = {
  name: 'a-textarea',
  props: ['value'],
  emits: ['update:value'],
  render() {
    return h('textarea', {
      class: 'stub-textarea',
      value: this.value,
      onInput: (e) => this.$emit('update:value', e.target.value),
    })
  },
}
const ButtonStub = {
  name: 'a-button',
  render() { return h('button', this.$attrs, this.$slots.default?.()) },
}
const TagStub = {
  name: 'a-tag',
  render() { return h('span', this.$slots.default?.()) },
}

const STUBS = {
  'a-radio-group': RadioGroupStub,
  'a-radio': RadioStub,
  'a-checkbox-group': CheckboxGroupStub,
  'a-checkbox': CheckboxStub,
  'a-textarea': TextareaStub,
  'a-button': ButtonStub,
  'a-tag': TagStub,
}

function mountCard(question) {
  setActivePinia(createPinia())
  const store = useChatStore()
  const send = vi.fn()
  store._ws = { readyState: 1, send }
  const w = mount(AskUserCard, {
    props: { ask: { questions: [question] } },
    global: { stubs: STUBS },
  })
  return { w, send }
}

const btn = (w, text) => w.findAll('button').find((b) => b.text().includes(text))

describe('AskUserCard 单题卡', () => {
  it('有选项的题也常驻人工回答输入框', () => {
    const { w } = mountCard({ question: '选哪个', options: [{ label: 'A' }, { label: 'B' }] })
    expect(w.find('.stub-textarea').exists()).toBe(true)
    expect(w.text()).toContain('或人工回答')
    expect(w.find('.stub-radio-group').exists()).toBe(true)
  })

  it('打字清空已选项，提交人工回答', async () => {
    const { w, send } = mountCard({ question: '选哪个', options: [{ label: 'A' }] })
    await w.find('.zc-opt').trigger('click')
    expect(w.findComponent(RadioGroupStub).props('value')).toBe('A')
    await w.find('.stub-textarea').setValue('我自己写')
    expect(w.findComponent(RadioGroupStub).props('value')).toBeUndefined()
    await btn(w, '提交回答').trigger('click')
    expect(JSON.parse(send.mock.calls[0][0])).toEqual({
      type: 'ask_submit', answers: [{ answer: '我自己写' }],
    })
  })

  it('点选项清空人工回答，提交选中项', async () => {
    const { w, send } = mountCard({ question: '选哪个', options: [{ label: 'A' }, { label: 'B' }] })
    await w.find('.stub-textarea').setValue('草稿')
    await w.findAll('.zc-opt')[1].trigger('click')
    expect(w.find('.stub-textarea').element.value).toBe('')
    await btn(w, '提交回答').trigger('click')
    expect(JSON.parse(send.mock.calls[0][0])).toEqual({
      type: 'ask_submit', answers: [{ options: ['B'] }],
    })
  })

  it('多选题提交多个选项', async () => {
    const { w, send } = mountCard({
      question: '选哪些', multi_select: true,
      options: [{ label: 'A' }, { label: 'B' }],
    })
    await w.findAll('.stub-checkbox')[0].trigger('click')
    await w.findAll('.stub-checkbox')[1].trigger('click')
    await btn(w, '提交回答').trigger('click')
    expect(JSON.parse(send.mock.calls[0][0])).toEqual({
      type: 'ask_submit', answers: [{ options: ['A', 'B'] }],
    })
  })

  it('「自行决定」上行固定答复，不是取消执行', async () => {
    const { w, send } = mountCard({ question: '选哪个', options: [{ label: 'A' }] })
    await btn(w, '自行决定').trigger('click')
    expect(send).toHaveBeenCalledTimes(1)
    const payload = JSON.parse(send.mock.calls[0][0])
    expect(payload.type).toBe('ask_submit')
    expect(payload.answers[0].answer).toContain('自行决定')
    expect(btn(w, '取消执行')).toBeTruthy()
  })

  it('无选项的题只有输入框，未答时提交禁用', async () => {
    const { w, send } = mountCard({ question: '叫什么名字？' })
    expect(w.find('.stub-radio-group').exists()).toBe(false)
    expect(w.text()).not.toContain('或人工回答')
    expect(btn(w, '提交回答').attributes('disabled')).toBeDefined()
    await w.find('.stub-textarea').setValue('小明')
    await btn(w, '提交回答').trigger('click')
    expect(JSON.parse(send.mock.calls[0][0]).answers).toEqual([{ answer: '小明' }])
  })
})
