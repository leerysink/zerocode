// @vitest-environment happy-dom
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { URL as NodeURL, fileURLToPath } from 'node:url'
import { h } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PreviewCard from './PreviewCard.vue'

const ButtonStub = {
  name: 'a-button',
  props: ['type', 'size'],
  render() {
    return h('button', { class: 'stub-button', onClick: () => this.$emit('click') },
      this.$slots.default ? this.$slots.default() : [])
  },
}

function mountCard(msg, progress = null) {
  setActivePinia(createPinia())
  return mount(PreviewCard, {
    props: { msg, progress },
    global: { stubs: { 'a-button': ButtonStub } },
  })
}

describe('PreviewCard 状态（规格 §8.2）', () => {
  it('进行中：蓝色读数 + 最近一条步骤行', () => {
    const w = mountCard({ role: 'tool', name: 'preview_restart', args: { wait_seconds: 240 } },
      { name: 'preview_restart', line: '[zerocode] → 执行 sqlchange 迁移' })
    expect(w.find('.zc-preview').classes()).toContain('is-info')
    expect(w.text()).toContain('正在重启预览')
    expect(w.text()).toContain('执行 sqlchange 迁移')
  })

  it('就绪：绿色 + 打开预览链接（href 逐字来自后端）', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: true, status: 'running', healthy: true, probe: '',
                url: 'http://10.0.0.5:8213', elapsed_s: 34.2, restarts_left: 2 },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-ok')
    expect(w.text()).toContain('预览已就绪')
    const link = w.find('.zc-preview-link')
    expect(link.attributes('href')).toBe('http://10.0.0.5:8213')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toBe('noopener')
  })

  it('未就绪：橙色 + probe 判定，不给链接', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_status',
      result: { ok: true, status: 'running', healthy: false, probe: '/ → 403',
                url: 'http://10.0.0.5:8213', hint: '端口探测未通过' },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-warn')
    expect(w.text()).toContain('预览未就绪')
    expect(w.text()).toContain('/ → 403')
    expect(w.find('.zc-preview-link').exists()).toBe(false)   // healthy=false 不给「打开」
  })

  it('preview_status 探到健康：绿色运行中读数 + 探测行 + 链接', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_status',
      result: { ok: true, status: 'running', healthy: true, probe: '/ → 200',
                url: 'http://10.0.0.5:8213', hint: '' },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-ok')
    expect(w.text()).toContain('预览运行中')
    expect(w.text()).toContain('探测：/ → 200')
    expect(w.find('.zc-preview-link').attributes('href')).toBe('http://10.0.0.5:8213')
  })

  it('等待超时：橙色「仍在启动」+ hint + 最近日志', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: false, status: 'starting', error: '', elapsed_s: 240,
                hint: '预览仍在启动，用 preview_status / preview_logs 继续观察；不要连续重启',
                logs: ['10:00:01 [平台] [zerocode] → docker compose up -d 启动 dev 栈'],
                restarts_left: 2 },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-warn')
    expect(w.text()).toContain('仍在启动')
    expect(w.text()).toContain('不要连续重启')
    expect(w.find('.zc-preview-logs').exists()).toBe(false)   // 日志默认折叠
    expect(w.find('.zc-preview-toggle').text()).toContain('查看日志')
  })

  it('取消等待：灰色 + hint', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: true, cancelled: true, status: 'starting', elapsed_s: 42,
                hint: '用户已停止本轮；预览仍在后台继续启动' },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-muted')
    expect(w.text()).toContain('已停止等待')
    expect(w.text()).toContain('后台继续启动')
  })

  it('失败：红色 + 错误原文 + 可折叠日志', async () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: false, status: 'error', error: 'dev 迁移失败（sqlrunner 退出码 1）',
                logs: ['10:00:03 [平台] [zerocode] ✗ 迁移失败，dev 栈不启动'], restarts_left: 2 },
    })
    expect(w.find('.zc-preview').classes()).toContain('is-error')
    expect(w.text()).toContain('预览操作失败')
    expect(w.text()).toContain('sqlrunner 退出码 1')
    expect(w.find('.zc-preview-logs').exists()).toBe(false)
    await w.find('.zc-preview-toggle').trigger('click')
    expect(w.find('.zc-preview-logs').text()).toContain('迁移失败')
  })

  it('日志工具：紧凑日志块 + 遮断计数 + note', async () => {
    const w = mountCard({
      role: 'tool', name: 'preview_logs',
      result: { ok: true, status: 'error', url: '', truncated: true, redacted: 2,
                note: '容器日志暂不可用：Docker 未启动',
                lines: ['10:00:01 [平台] [zerocode] ✗ 启动预览失败',
                        '10:00:02 [backend] <redacted:db_password>'] },
    })
    expect(w.text()).toContain('预览日志')
    expect(w.text()).toContain('已遮断 2 处凭据')
    expect(w.text()).toContain('Docker 未启动')
    await w.find('.zc-preview-toggle').trigger('click')
    expect(w.findAll('.zc-preview-logs li').length).toBe(2)
  })

  it('剩余次数 <=1 时给预警', () => {
    const w = mountCard({
      role: 'tool', name: 'preview_restart',
      result: { ok: true, status: 'running', healthy: true, url: 'http://h:1', restarts_left: 1 },
    })
    expect(w.text()).toContain('本次任务还可重启 1 次')
  })

  it('历史消息（只有 tool_name / tool_result，没有 args）也能渲染', () => {
    const w = mountCard({
      role: 'tool', tool_name: 'preview_status',
      tool_result: { ok: true, status: 'stopped', healthy: false, probe: '', url: '', hint: '预览未运行' },
    })
    expect(w.text()).toContain('预览未就绪')
    expect(w.text()).toContain('预览未运行')
  })
})

describe('PreviewCard 红线', () => {
  it('源码不出现预览地址的拼接形态（地址只能来自后端字段）', () => {
    // 用 node 的 URL 构造函数：happy-dom 把全局 URL 换成了自己的实现，它造出来的对象 node:fs 不认
    // （同仓 DbAdminView.test.js 的样式闸撞过同一面墙，那里改成了候选路径探测）。
    // 断的是整份源码、注释也算：口径写进注释后如果留下「例如拼个什么地址」的反例，
    // 迟早被人照抄进代码，所以宁可连注释一起拦。
    const src = readFileSync(fileURLToPath(new NodeURL('./PreviewCard.vue', import.meta.url)), 'utf8')
    expect(src.length).toBeGreaterThan(0)   // 读空了就当失败，免得三条 not 断言静默空跑
    expect(src).not.toMatch(/localhost/)
    expect(src).not.toMatch(/:\/\//)
    expect(src).not.toMatch(/dev_port/)
  })
})
