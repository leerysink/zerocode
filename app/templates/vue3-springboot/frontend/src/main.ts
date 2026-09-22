import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Antd from 'ant-design-vue'
import * as AntIcons from '@ant-design/icons-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import router from './router'
import './style.css'
import './style/theme.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(Antd)

// 全局注册 Ant Design Vue 图标组件
Object.keys(AntIcons).forEach((key) => {
  app.component(key, (AntIcons as any)[key])
})

app.mount('#app')
