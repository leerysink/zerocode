// 移动栈的判定点：卡面 ☰ 与聊天页「更多」都读这里，避免两处各写一份 stack key。
// 栈的真源在后端 app/deploy/compose.py::MOBILE_STACK。
export const MOBILE_STACK = 'vue3-springboot-mobile'

export const isMobileApp = (app) => app?.stack === MOBILE_STACK
