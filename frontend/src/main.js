import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import router from './router'
import './styles/theme.less'
import './styles/db-admin.less'   // DB 操作台专用样式，全部锁在 .zc-db / .zc-db-drawer 作用域内
createApp(App).use(createPinia()).use(router).use(Antd).mount('#app')
