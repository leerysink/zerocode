// @vitest-environment happy-dom
// run_python 回执卡技能脚本模式（ADR-0061 / ticket 005）：result.script 区分标题+脚本名。
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import RunPythonCard from './RunPythonCard.vue'

function mountCard(result) {
  return mount(RunPythonCard, { props: { msg: { name: 'run_python', result } } })
}

describe('RunPythonCard 技能脚本回执', () => {
  it('skill 模式（result.script）→ 标题区分 + 脚本路径', () => {
    const w = mountCard({ ok: true, exit_code: 0, output: 'hi', script: 'scripts/run.py', artifacts: [] })
    expect(w.find('.zc-page-title').text()).toBe('技能脚本已执行')
    expect(w.find('[data-test="python-script"]').text()).toContain('scripts/run.py')
  })

  it('skill 非零退出 → 标题带非零', () => {
    const w = mountCard({ ok: true, exit_code: 1, output: 'err', script: 'scripts/run.py', artifacts: [] })
    expect(w.find('.zc-page-title').text()).toBe('技能脚本已执行（非零退出）')
  })

  it('skill 平台失败 → 技能脚本执行失败', () => {
    const w = mountCard({ ok: false, error: 'docker 不可用', script: 'scripts/run.py' })
    expect(w.find('.zc-page-title').text()).toBe('技能脚本执行失败')
  })

  it('code 模式（无 script）→ 标题不变（回归）', () => {
    const w = mountCard({ ok: true, exit_code: 0, output: '2', artifacts: [] })
    expect(w.find('.zc-page-title').text()).toBe('Python 脚本已执行')
    expect(w.find('[data-test="python-script"]').exists()).toBe(false)
  })
})