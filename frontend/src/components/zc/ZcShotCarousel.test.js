// @vitest-environment happy-dom
// 卡片封面截图轮播契约：多图自动切换并循环、悬停暂停/移开恢复、单图与减动效静止、
// 换一批截图回到首张、卸载时清定时器。
// happy-dom 不做布局也不跑过渡计时，这里以「指示点 + DOM 中是否存在对应 src 的图」
// 判定当前张，不依赖过渡中间态。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'

import ZcShotCarousel from './ZcShotCarousel.vue'

const SHOTS = ['/s/0.png', '/s/1.png', '/s/2.png']
const STEP = 3000

function mountCarousel (shots = SHOTS) {
  return mount(ZcShotCarousel, { props: { shots } })
}
const activeDot = (w) => w.findAll('.zc-shot-dots i')
  .findIndex((d) => d.classes().includes('on'))
const shown = (w, src) => w.findAll('img').some((i) => i.attributes('src') === src)

let realMatchMedia

beforeEach(() => {
  vi.useFakeTimers()
  realMatchMedia = window.matchMedia
})

afterEach(() => {
  window.matchMedia = realMatchMedia
  vi.useRealTimers()
})

describe('ZcShotCarousel', () => {
  it('多图：每 3 秒切下一张，末张后回到首张', async () => {
    const w = mountCarousel()
    expect(shown(w, SHOTS[0])).toBe(true)
    expect(activeDot(w)).toBe(0)

    await vi.advanceTimersByTimeAsync(STEP)
    await nextTick()
    expect(activeDot(w)).toBe(1)
    expect(shown(w, SHOTS[1])).toBe(true)

    await vi.advanceTimersByTimeAsync(STEP)
    await nextTick()
    expect(activeDot(w)).toBe(2)

    await vi.advanceTimersByTimeAsync(STEP)
    await nextTick()
    expect(activeDot(w)).toBe(0)
    w.unmount()
  })

  it('悬停暂停轮播，移开后恢复', async () => {
    const w = mountCarousel()
    const root = w.find('[data-test="shot-carousel"]')
    await root.trigger('mouseenter')
    await vi.advanceTimersByTimeAsync(STEP * 3)
    await nextTick()
    expect(activeDot(w)).toBe(0)

    await root.trigger('mouseleave')
    await vi.advanceTimersByTimeAsync(STEP)
    await nextTick()
    expect(activeDot(w)).toBe(1)
    w.unmount()
  })

  it('单图：不出指示点，也不起定时器', async () => {
    const w = mountCarousel(['/only.png'])
    expect(w.find('.zc-shot-dots').exists()).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(STEP * 2)
    await nextTick()
    expect(shown(w, '/only.png')).toBe(true)
    w.unmount()
  })

  it('减动效偏好：不自动切换，指示点仍标出当前位置', async () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true })
    const w = mountCarousel()
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(STEP * 3)
    await nextTick()
    expect(activeDot(w)).toBe(0)
    w.unmount()
  })

  it('换一批截图：回到首张并按新图数继续轮播', async () => {
    const w = mountCarousel()
    await vi.advanceTimersByTimeAsync(STEP)
    await nextTick()
    expect(activeDot(w)).toBe(1)

    await w.setProps({ shots: ['/n/0.png', '/n/1.png'] })
    await nextTick()
    expect(activeDot(w)).toBe(0)
    expect(w.findAll('.zc-shot-dots i')).toHaveLength(2)
    expect(shown(w, '/n/0.png')).toBe(true)

    await vi.advanceTimersByTimeAsync(STEP)
    await nextTick()
    expect(activeDot(w)).toBe(1)
    w.unmount()
  })

  it('卸载时清掉定时器', async () => {
    const w = mountCarousel()
    expect(vi.getTimerCount()).toBe(1)
    w.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
