<template>
  <div class="login-page">
    <!-- ===== 全屏构建剧场 ===== -->
    <div class="scene" aria-hidden="true">
      <div class="aurora aurora-cyan"></div>
      <div class="aurora aurora-amber"></div>
      <div class="aurora aurora-violet"></div>
      <div class="stars">
        <i v-for="p in particles" :key="p.id"
           :style="{ left: p.left + '%', top: p.top + '%', width: p.size + 'px', height: p.size + 'px',
                     animationDelay: p.delay + 's', animationDuration: p.dur + 's' }"></i>
      </div>
      <div class="grid-floor"></div>
      <div class="noise"></div>
      <div class="edge-word">ZEROCODE</div>
    </div>

    <!-- ===== 左侧：品牌叙事 + 生成终端 ===== -->
    <aside class="stage-side">
      <header class="brand-row">
        <div class="brand">
          <svg class="brand-mark" viewBox="0 0 32 32" fill="none">
            <rect x="3" y="17" width="12" height="12" rx="3" fill="#4cc3ff" />
            <rect x="17" y="17" width="12" height="12" rx="3" fill="#5b7a9d" />
            <rect x="10" y="3" width="12" height="12" rx="3" fill="#f5b04c" />
          </svg>
          <span class="brand-word">zerocode</span>
        </div>
        <span class="brand-tag">私有部署 · 数据自主可控</span>
      </header>

      <div class="hero">
        <div class="cube-field">
          <div class="cube cube-a"><i v-for="f in 6" :key="f" :class="'face f' + f"></i></div>
          <div class="cube cube-b"><i v-for="f in 6" :key="f" :class="'face f' + f"></i></div>
          <div class="cube cube-c"><i v-for="f in 6" :key="f" :class="'face f' + f"></i></div>
          <div class="cube-shadow"></div>
        </div>
        <p class="hero-kicker">ZERO → ONE</p>
        <h1 class="hero-title">一句话，<br />从 <span class="zero">0</span> 到 <span class="one">1</span>。</h1>
        <p class="hero-sub">描述你想要的东西，zerocode 负责生成、部署、上线。<br />不写一行代码，你的想法直接变成能用的应用。</p>
      </div>

      <div class="terminal">
        <div class="term-bar">
          <span class="term-dot r"></span><span class="term-dot y"></span><span class="term-dot g"></span>
          <span class="term-title">zerocode · 生成实录</span>
          <span class="term-live-flag" :class="{ on: termLive }">{{ termLive ? 'LIVE' : 'BUILDING' }}</span>
        </div>
        <div class="term-body">
          <div class="term-prompt"><span class="term-ps">›</span>{{ termText }}<span class="caret" :class="{ idle: termSteps > 0 }"></span></div>
          <div class="term-steps">
            <div v-for="(s, i) in TERM_STEPS" :key="s" class="term-step" :class="{ show: termSteps > i }">
              <svg viewBox="0 0 12 12" fill="none"><path d="M2 6.2 4.8 9 10 3.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
              <span>{{ s }}</span>
            </div>
          </div>
          <div class="term-url" :class="{ show: termLive }">
            <i></i>已上线 · booking.zerocode.app
          </div>
        </div>
      </div>

      <div class="ticker">
        <div class="ticker-track">
          <span v-for="n in 2" :key="n" class="ticker-group">
            <em v-for="t in TICKER" :key="t">{{ t }}</em>
          </span>
        </div>
      </div>
    </aside>

    <!-- ===== 右侧：玻璃表单 ===== -->
    <main class="form-side">
      <div class="login-card">
        <div class="card-glow"></div>
        <div class="mode-seg" role="tablist" aria-label="登录或注册">
          <i class="seg-thumb" :class="{ right: mode === 'register' }"></i>
          <button type="button" role="tab" :aria-selected="mode === 'login'"
                  :class="{ on: mode === 'login' }" @click="setMode('login')">登录</button>
          <button type="button" role="tab" :aria-selected="mode === 'register'"
                  :class="{ on: mode === 'register' }" @click="setMode('register')">注册</button>
        </div>

        <div class="login-head">
          <h2 class="login-title">{{ mode === 'login' ? '欢迎回来' : '创建你的账号' }}</h2>
          <p class="login-subtitle">{{ mode === 'login' ? '只需昵称和口令，两步进入工作台。' : '一个账号，开始构建你的第一个应用。' }}</p>
        </div>

        <!-- :model 必须绑：antd Form 无 model 时 finish 永不触发，登录/注册按钮会静默失效（Ticket 010 冒烟回归） -->
        <a-form :model="form" layout="vertical" @finish="submit">
          <a-form-item name="nickname" label="昵称">
            <a-input v-model:value="form.nickname" placeholder="怎么称呼你" size="large" autocomplete="username" />
          </a-form-item>
          <a-form-item name="password" label="口令">
            <a-input-password v-model:value="form.password" placeholder="登录口令" size="large"
                              :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" />
          </a-form-item>
          <p v-if="mode === 'register'" class="pwd-hint">口令用于以后登录，请牢记。</p>
          <a-alert v-if="error" type="error" show-icon :message="error" class="login-error" />
          <a-button type="primary" html-type="submit" block size="large" :loading="loading" class="login-submit">
            {{ mode === 'login' ? '进入工作台' : '注册并进入' }}
          </a-button>
        </a-form>

        <p class="card-foot">{{ mode === 'login' ? '还没有账号？切到「注册」即可创建。' : '已有账号？切到「登录」直接进入。' }}</p>
      </div>
      <p class="side-foot">zerocode · 让每个人都能构建应用</p>
    </main>
  </div>
</template>

<script setup>
// 登录/注册页（Ticket 002 / spec 故事 1/2/4/14）：只有昵称+口令两个字段，零经验友好。
import { reactive, ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const mode = ref('login')
const loading = ref(false)
const error = ref('')
const form = reactive({ nickname: '', password: '' })

function setMode(m) {
  mode.value = m
  error.value = ''
}

async function submit() {
  loading.value = true
  error.value = ''
  try {
    if (mode.value === 'login') {
      await auth.login({ nickname: form.nickname, password: form.password })
    } else {
      await auth.register({ nickname: form.nickname, password: form.password })
    }
    router.replace(typeof route.query.next === 'string' ? route.query.next : '/')
  } catch (e) {
    error.value = e.response?.data?.detail || '操作失败，请稍后再试'
  } finally {
    loading.value = false
  }
}

// ---- 装饰层：粒子 / 生成实录循环 ----
const TICKER = ['对话生成', '一键部署', '独立域名', '应用商店', '数据库管理', '资源监控', '团队协作', 'HTTPS 网关']
const TERM_STEPS = ['生成数据表 courses / bookings', '搭建页面与接口', '构建镜像，部署上线']
const PROMPT = '做一个带数据库的课程预约应用'

const particles = Array.from({ length: 26 }, (_, i) => ({
  id: i,
  left: Math.random() * 100,
  top: Math.random() * 100,
  size: 1.5 + Math.random() * 2.5,
  delay: -Math.random() * 12,
  dur: 8 + Math.random() * 10,
}))

const termText = ref('')
const termSteps = ref(0)
const termLive = ref(false)
let termCancelled = false
let termTimer = null
let termWake = null

const wait = (ms) => new Promise((r) => { termWake = r; termTimer = setTimeout(r, ms) })

async function termLoop() {
  while (!termCancelled) {
    termText.value = ''
    termSteps.value = 0
    termLive.value = false
    for (const ch of PROMPT) {
      if (termCancelled) return
      termText.value += ch
      await wait(60)
    }
    await wait(450)
    for (let i = 1; i <= TERM_STEPS.length; i++) {
      if (termCancelled) return
      termSteps.value = i
      await wait(800)
    }
    if (termCancelled) return
    termLive.value = true
    await wait(3400)
  }
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    termText.value = PROMPT
    termSteps.value = TERM_STEPS.length
    termLive.value = true
    return
  }
  termLoop()
})
onUnmounted(() => {
  termCancelled = true
  clearTimeout(termTimer)
  if (termWake) termWake()
})
</script>

<style scoped>
.login-page {
  position: relative;
  min-height: 100vh;
  min-height: 100dvh;
  display: grid;
  grid-template-columns: minmax(520px, 1.3fr) minmax(420px, 1fr);
  background: #070d18;
  overflow: hidden;
}

/* ================= 场景层 ================= */
.scene { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.aurora { position: absolute; border-radius: 50%; filter: blur(80px); }
.aurora-cyan {
  width: 640px; height: 640px; left: -180px; top: -220px;
  background: radial-gradient(circle, rgba(56, 189, 248, 0.30), rgba(56, 189, 248, 0) 66%);
  animation: drift-a 18s ease-in-out infinite alternate;
}
.aurora-amber {
  width: 560px; height: 560px; right: -140px; top: 22%;
  background: radial-gradient(circle, rgba(245, 176, 76, 0.17), rgba(245, 176, 76, 0) 66%);
  animation: drift-a 23s ease-in-out infinite alternate-reverse;
}
.aurora-violet {
  width: 700px; height: 700px; left: 30%; bottom: -320px;
  background: radial-gradient(circle, rgba(124, 108, 240, 0.18), rgba(91, 122, 157, 0.14) 48%, rgba(91, 122, 157, 0) 72%);
  animation: drift-a 26s ease-in-out infinite alternate;
}
@keyframes drift-a {
  from { transform: translate3d(0, 0, 0) scale(1); }
  to { transform: translate3d(60px, 40px, 0) scale(1.14); }
}
.stars i {
  position: absolute;
  border-radius: 50%;
  background: #9fd0f5;
  opacity: 0;
  animation: twinkle linear infinite;
}
@keyframes twinkle {
  0%, 100% { opacity: 0; transform: translateY(0); }
  18% { opacity: 0.85; }
  55% { opacity: 0.25; }
  80% { opacity: 0.7; transform: translateY(-26px); }
}
.grid-floor {
  position: absolute;
  left: -22%; right: -22%; bottom: -14%;
  height: 56%;
  background-image:
    linear-gradient(rgba(96, 165, 220, 0.20) 1px, transparent 1px),
    linear-gradient(90deg, rgba(96, 165, 220, 0.20) 1px, transparent 1px);
  background-size: 46px 46px;
  transform: perspective(620px) rotateX(62deg);
  transform-origin: center top;
  mask-image: linear-gradient(to bottom, transparent 0%, #000 34%, #000 78%, transparent 100%);
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, #000 34%, #000 78%, transparent 100%);
  animation: floor-move 3.2s linear infinite;
}
@keyframes floor-move {
  from { background-position: 0 0, 0 0; }
  to { background-position: 0 46px, 0 0; }
}
.noise {
  position: absolute; inset: 0;
  opacity: 0.05;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)' opacity='0.6'/%3E%3C/svg%3E");
}
.edge-word {
  position: absolute;
  left: -8px; top: 50%;
  transform: translateY(-50%) rotate(180deg);
  writing-mode: vertical-rl;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.62em;
  color: rgba(126, 168, 210, 0.30);
  user-select: none;
}

/* ================= 左侧叙事 ================= */
.stage-side {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  padding: clamp(26px, 4.5vh, 52px) clamp(30px, 4.5vw, 68px) 0 clamp(44px, 5vw, 84px);
  min-width: 0;
}
.brand-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  animation: rise 0.6s cubic-bezier(0.22, 1, 0.36, 1) both;
}
.brand { display: flex; align-items: center; gap: 10px; }
.brand-mark { width: 30px; height: 30px; filter: drop-shadow(0 0 12px rgba(76, 195, 255, 0.45)); }
.brand-word { font-size: 20px; font-weight: 700; letter-spacing: 0.02em; color: #f0f6fc; }
.brand-tag {
  padding: 5px 13px;
  border-radius: 20px;
  font-size: 12px;
  color: #9fc3e2;
  border: 1px solid rgba(110, 168, 220, 0.30);
  background: rgba(20, 38, 60, 0.55);
  white-space: nowrap;
}

.hero { position: relative; margin-top: clamp(30px, 7vh, 84px); animation: rise 0.6s 0.08s cubic-bezier(0.22, 1, 0.36, 1) both; }
.hero-kicker {
  margin: 0 0 14px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.42em;
  color: #4cc3ff;
}
.hero-title {
  margin: 0;
  font-size: clamp(38px, 4.6vw, 66px);
  line-height: 1.16;
  font-weight: 800;
  letter-spacing: 0.01em;
  color: #f4f8fd;
}
.hero-title .zero {
  color: transparent;
  -webkit-text-stroke: 2px rgba(159, 208, 245, 0.85);
}
.hero-title .one {
  background: linear-gradient(100deg, #4cc3ff 15%, #8fd6ff 50%, #f5b04c 105%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  filter: drop-shadow(0 0 18px rgba(76, 195, 255, 0.35));
}
.hero-sub {
  margin: 18px 0 0;
  font-size: 14.5px;
  line-height: 1.95;
  color: #a3b7cd;
}

/* ---- CSS 3D 方块塔 ---- */
.cube-field {
  position: absolute;
  right: clamp(-10px, 1vw, 30px);
  top: -34px;
  width: 200px; height: 230px;
  perspective: 900px;
  pointer-events: none;
}
.cube { position: absolute; transform-style: preserve-3d; }
.cube .face {
  position: absolute;
  inset: 0;
  border: 1px solid rgba(120, 190, 245, 0.55);
  background:
    linear-gradient(rgba(96, 165, 220, 0.16) 1px, transparent 1px),
    linear-gradient(90deg, rgba(96, 165, 220, 0.16) 1px, transparent 1px),
    rgba(28, 52, 82, 0.30);
  background-size: 12px 12px, 12px 12px, auto;
  box-shadow: inset 0 0 22px rgba(76, 195, 255, 0.14);
}
.cube-a { width: 96px; height: 96px; left: 46px; top: 44px; animation: spin-a 16s linear infinite; }
.cube-a .f1 { transform: translateZ(48px); }
.cube-a .f2 { transform: rotateY(180deg) translateZ(48px); }
.cube-a .f3 { transform: rotateY(90deg) translateZ(48px); }
.cube-a .f4 { transform: rotateY(-90deg) translateZ(48px); }
.cube-a .f5 { transform: rotateX(90deg) translateZ(48px); }
.cube-a .f6 { transform: rotateX(-90deg) translateZ(48px); }
.cube-b { width: 58px; height: 58px; left: 6px; top: 130px; animation: spin-b 12s linear infinite; }
.cube-b .face { border-color: rgba(245, 176, 76, 0.6); background-color: rgba(74, 56, 24, 0.26); box-shadow: inset 0 0 18px rgba(245, 176, 76, 0.16); }
.cube-b .f1 { transform: translateZ(29px); }
.cube-b .f2 { transform: rotateY(180deg) translateZ(29px); }
.cube-b .f3 { transform: rotateY(90deg) translateZ(29px); }
.cube-b .f4 { transform: rotateY(-90deg) translateZ(29px); }
.cube-b .f5 { transform: rotateX(90deg) translateZ(29px); }
.cube-b .f6 { transform: rotateX(-90deg) translateZ(29px); }
.cube-c { width: 40px; height: 40px; left: 142px; top: 8px; animation: spin-a 9s linear infinite reverse; }
.cube-c .face { border-color: rgba(61, 220, 151, 0.6); background-color: rgba(20, 56, 44, 0.28); box-shadow: inset 0 0 14px rgba(61, 220, 151, 0.18); }
.cube-c .f1 { transform: translateZ(20px); }
.cube-c .f2 { transform: rotateY(180deg) translateZ(20px); }
.cube-c .f3 { transform: rotateY(90deg) translateZ(20px); }
.cube-c .f4 { transform: rotateY(-90deg) translateZ(20px); }
.cube-c .f5 { transform: rotateX(90deg) translateZ(20px); }
.cube-c .f6 { transform: rotateX(-90deg) translateZ(20px); }
@keyframes spin-a {
  from { transform: rotateX(-22deg) rotateY(0deg); }
  to { transform: rotateX(-22deg) rotateY(360deg); }
}
@keyframes spin-b {
  from { transform: rotateX(18deg) rotateY(360deg); }
  to { transform: rotateX(18deg) rotateY(0deg); }
}
.cube-shadow {
  position: absolute;
  left: 30px; bottom: 6px;
  width: 150px; height: 26px;
  border-radius: 50%;
  background: radial-gradient(ellipse, rgba(4, 10, 20, 0.6), transparent 70%);
  filter: blur(4px);
}

/* ---- 生成实录终端 ---- */
.terminal {
  margin-top: clamp(26px, 5vh, 54px);
  max-width: 480px;
  border-radius: 14px;
  border: 1px solid rgba(110, 168, 220, 0.26);
  background: linear-gradient(168deg, rgba(17, 31, 51, 0.92), rgba(9, 17, 30, 0.94));
  box-shadow: 0 24px 60px rgba(2, 6, 14, 0.55), inset 0 1px 0 rgba(170, 210, 245, 0.10);
  overflow: hidden;
  animation: rise 0.6s 0.18s cubic-bezier(0.22, 1, 0.36, 1) both;
}
.term-bar {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(110, 168, 220, 0.16);
  background: rgba(13, 24, 40, 0.7);
}
.term-dot { width: 9px; height: 9px; border-radius: 50%; }
.term-dot.r { background: #f26d6d; }
.term-dot.y { background: #f5b04c; }
.term-dot.g { background: #3ddc97; }
.term-title { margin-left: 8px; font-size: 12px; color: #7e96b0; letter-spacing: 0.05em; }
.term-live-flag {
  margin-left: auto;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: #f5b04c;
  transition: color 0.3s;
}
.term-live-flag.on { color: #3ddc97; }
.term-body { padding: 16px 18px 18px; font-family: 'Cascadia Code', 'JetBrains Mono', Consolas, monospace; }
.term-prompt { font-size: 13.5px; color: #e8f1fa; min-height: 22px; line-height: 22px; }
.term-ps { color: #4cc3ff; margin-right: 9px; font-weight: 700; }
.caret {
  display: inline-block;
  width: 8px; height: 16px;
  margin-left: 3px;
  vertical-align: -2px;
  background: #4cc3ff;
  animation: blink 1s steps(1) infinite;
}
.caret.idle { animation: none; opacity: 0; }
@keyframes blink { 50% { opacity: 0; } }
.term-steps { margin-top: 12px; display: grid; gap: 8px; }
.term-step {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 12.5px;
  color: #93a9c0;
  opacity: 0;
  transform: translateY(6px);
  transition: opacity 0.35s, transform 0.35s;
}
.term-step.show { opacity: 1; transform: translateY(0); }
.term-step svg { width: 13px; height: 13px; color: #3ddc97; flex: none; }
.term-url {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  padding: 5px 13px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  color: #3ddc97;
  border: 1px solid rgba(61, 220, 151, 0.35);
  background: rgba(61, 220, 151, 0.10);
  opacity: 0;
  transform: translateY(6px);
  transition: opacity 0.4s, transform 0.4s;
}
.term-url.show { opacity: 1; transform: translateY(0); }
.term-url i {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: #3ddc97;
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(61, 220, 151, 0.5); }
  60% { box-shadow: 0 0 0 6px rgba(61, 220, 151, 0); }
}

/* ---- 能力跑马灯 ---- */
.ticker {
  margin-top: auto;
  padding: 20px 0 22px;
  overflow: hidden;
  mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent);
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 12%, #000 88%, transparent);
}
.ticker-track { display: flex; width: max-content; animation: tick 26s linear infinite; }
.ticker-group { display: flex; gap: 12px; padding-right: 12px; }
.ticker em {
  font-style: normal;
  padding: 5px 15px;
  border-radius: 20px;
  font-size: 12px;
  color: #9db6cf;
  border: 1px solid rgba(110, 168, 220, 0.24);
  background: rgba(16, 30, 50, 0.6);
  white-space: nowrap;
}
@keyframes tick { from { transform: translateX(0); } to { transform: translateX(-50%); } }

/* ================= 右侧玻璃表单 ================= */
.form-side {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 44px 26px;
  overflow-y: auto;
  background:
    radial-gradient(ellipse 80% 50% at 50% 0%, rgba(56, 189, 248, 0.09), transparent 100%),
    linear-gradient(180deg, rgba(10, 19, 33, 0.5), rgba(7, 13, 24, 0.82));
  border-left: 1px solid rgba(110, 168, 220, 0.14);
  scrollbar-width: thin;
  scrollbar-color: #2c4560 transparent;
}
.form-side::-webkit-scrollbar { width: 8px; }
.form-side::-webkit-scrollbar-track { background: transparent; }
.form-side::-webkit-scrollbar-thumb { background: #2c4560; border-radius: 4px; }
.form-side::-webkit-scrollbar-thumb:hover { background: #4cc3ff; }

.login-card {
  position: relative;
  width: 100%;
  max-width: 408px;
  padding: 30px 32px 26px;
  border-radius: 18px;
  border: 1px solid rgba(120, 180, 235, 0.22);
  background: linear-gradient(172deg, rgba(19, 34, 56, 0.88), rgba(10, 19, 33, 0.92));
  backdrop-filter: blur(14px);
  box-shadow: 0 30px 80px rgba(2, 6, 14, 0.6), inset 0 1px 0 rgba(170, 210, 245, 0.12);
  animation: rise 0.6s 0.12s cubic-bezier(0.22, 1, 0.36, 1) both;
}
.card-glow {
  position: absolute;
  left: 12%; right: 12%; top: -1px;
  height: 2px;
  border-radius: 2px;
  background: linear-gradient(90deg, transparent, #4cc3ff 30%, #f5b04c 72%, transparent);
  filter: drop-shadow(0 0 10px rgba(76, 195, 255, 0.7));
}

.mode-seg {
  position: relative;
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding: 4px;
  border-radius: 12px;
  border: 1px solid rgba(110, 168, 220, 0.22);
  background: rgba(8, 15, 27, 0.7);
  margin-bottom: 24px;
}
.seg-thumb {
  position: absolute;
  top: 4px; bottom: 4px; left: 4px;
  width: calc(50% - 4px);
  border-radius: 9px;
  background: linear-gradient(135deg, rgba(76, 195, 255, 0.26), rgba(91, 122, 157, 0.34));
  border: 1px solid rgba(120, 190, 245, 0.45);
  box-shadow: 0 4px 14px rgba(56, 189, 248, 0.22);
  transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.seg-thumb.right { transform: translateX(100%); }
.mode-seg button {
  position: relative;
  z-index: 1;
  padding: 9px 0;
  border: none;
  border-radius: 9px;
  background: none;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.1em;
  color: #7e96b0;
  cursor: pointer;
  transition: color 0.25s;
}
.mode-seg button.on { color: #eaf4fc; }
.mode-seg button:focus-visible { outline: 2px solid #4cc3ff; outline-offset: 2px; }

.login-head { margin-bottom: 22px; }
.login-title { margin: 0; font-size: 24px; font-weight: 700; color: #f2f7fd; letter-spacing: 0.01em; }
.login-subtitle { margin: 8px 0 0; font-size: 13px; line-height: 1.75; color: #8ba1b9; }

.login-card :deep(.ant-form-item-label > label) {
  font-size: 13px;
  font-weight: 600;
  color: #c3d3e4;
}
.login-card :deep(.ant-input),
.login-card :deep(.ant-input-affix-wrapper) {
  border-radius: 11px;
  border-color: rgba(110, 168, 220, 0.26);
  background: rgba(8, 15, 27, 0.72);
  color: #eaf2fa;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
}
.login-card :deep(.ant-input::placeholder) { color: #5c718a; }
.login-card :deep(.ant-input:hover),
.login-card :deep(.ant-input-affix-wrapper:hover) { border-color: rgba(120, 190, 245, 0.5); }
.login-card :deep(.ant-input:focus),
.login-card :deep(.ant-input-affix-wrapper-focused) {
  background: rgba(10, 20, 36, 0.9);
  border-color: #4cc3ff;
  box-shadow: 0 0 0 3px rgba(76, 195, 255, 0.16);
}
.login-card :deep(.ant-input-affix-wrapper .ant-input) { background: transparent; box-shadow: none; }
.login-card :deep(.ant-input-password-icon) { color: #64809c; }
.login-card :deep(.ant-input-password-icon:hover) { color: #4cc3ff; }
.login-card :deep(.ant-input:-webkit-autofill) {
  -webkit-box-shadow: 0 0 0 100px #0b1626 inset;
  -webkit-text-fill-color: #eaf2fa;
}

.pwd-hint { margin: -6px 0 14px; font-size: 12px; color: #7e96b0; }
.login-error {
  margin-bottom: 14px;
  border-radius: 11px;
  border: 1px solid rgba(242, 109, 109, 0.4);
  background: rgba(120, 30, 30, 0.24);
}
.login-error :deep(.ant-alert-message) { color: #ffb4b4; }
.login-error :deep(.ant-alert-icon) { color: #f26d6d; }

.login-submit {
  position: relative;
  height: 46px;
  margin-top: 4px;
  border: none;
  border-radius: 12px;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: #06121f;
  background: linear-gradient(120deg, #4cc3ff 0%, #3d9be0 52%, #6d8fb4 115%);
  box-shadow: 0 10px 28px rgba(56, 189, 248, 0.32), inset 0 1px 0 rgba(255, 255, 255, 0.35);
  overflow: hidden;
  transition: transform 0.15s, box-shadow 0.2s, filter 0.2s;
}
.login-submit::after {
  content: '';
  position: absolute;
  top: 0; bottom: 0;
  left: -70%;
  width: 44%;
  background: linear-gradient(100deg, transparent, rgba(255, 255, 255, 0.5), transparent);
  transform: skewX(-22deg);
  transition: left 0.55s ease;
}
.login-submit:hover {
  background: linear-gradient(120deg, #4cc3ff 0%, #3d9be0 52%, #6d8fb4 115%);
  color: #06121f;
  filter: brightness(1.07);
  transform: translateY(-1px);
  box-shadow: 0 14px 34px rgba(56, 189, 248, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.35);
}
.login-submit:hover::after { left: 130%; }
.login-submit:active { transform: translateY(0); filter: brightness(0.95); }
.login-submit :deep(.ant-btn-loading-icon) { color: #06121f; }

.card-foot {
  margin: 20px 0 0;
  padding-top: 16px;
  border-top: 1px dashed rgba(110, 168, 220, 0.22);
  text-align: center;
  font-size: 12.5px;
  color: #7e96b0;
}
.side-foot {
  margin: 22px 0 0;
  font-size: 12px;
  letter-spacing: 0.06em;
  color: #5c718a;
  animation: rise 0.6s 0.24s cubic-bezier(0.22, 1, 0.36, 1) both;
}

@keyframes rise {
  from { opacity: 0; transform: translateY(18px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ================= 响应式 ================= */
@media (max-width: 1240px) {
  .cube-field { opacity: 0.5; right: -30px; }
}
@media (max-width: 1080px) {
  .cube-field { display: none; }
}
@media (max-width: 959px) {
  .login-page { grid-template-columns: 1fr; overflow: visible; }
  .stage-side { padding: 26px 20px 0; }
  .hero { margin-top: 26px; }
  .hero-title { font-size: clamp(30px, 8vw, 42px); }
  .hero-sub br { display: none; }
  .terminal { margin-top: 22px; max-width: none; }
  .ticker { display: none; }
  .brand-tag { display: none; }
  .edge-word { display: none; }
  .form-side { border-left: none; padding: 28px 18px 36px; overflow: visible; }
  .grid-floor { height: 34%; }
}
@media (max-width: 420px) {
  .login-card { padding: 24px 18px 20px; }
  .term-body { padding: 13px 14px 15px; }
}
@media (max-height: 820px) and (min-width: 960px) {
  .hero { margin-top: 22px; }
  .hero-title { font-size: clamp(32px, 3.6vw, 48px); }
  .terminal { margin-top: 20px; }
  .ticker { padding: 14px 0 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .aurora, .stars i, .grid-floor, .cube, .ticker-track,
  .caret, .term-url i, .brand-row, .hero, .terminal, .login-card, .side-foot {
    animation: none;
  }
  .stars i { opacity: 0.5; }
  .term-step, .term-url { transition: none; }
}
</style>