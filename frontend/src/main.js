import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import router from './router'
import './styles/theme.less'
import './styles/db-admin.less'   // DB 操作台专用样式，全部锁在 .zc-db / .zc-db-drawer 作用域内
import { useThemeStore, applyTheme } from './stores/theme'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(Antd)
// 主题在挂载前落到 <html data-theme>：深色用户刷新不闪浅色帧（US-16）。
// store 初始化即唯一一次存储读数（含旧键一次性继承），此处复用同一读数。
const themeStore = useThemeStore()
applyTheme(themeStore.theme)
window.addEventListener('storage', (e) => themeStore.syncFromStorage(e))
app.mount('#app')
