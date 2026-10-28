// 可发布栈名单（前端镜像）。唯一真相在后端 `app/deploy/compose.py::PUBLISHABLE_STACKS`，
// 漂移由 `tests/test_deploy_basics.py` 钉住。
//
// UI 这道拦仍要留：后端 render_prod_compose 对不可发布栈抛 400，但发布是 202 后台任务，
// 400 到不了 HTTP，只会把界面变成「出错了」。
//
// 名单只能在这里写一次：应用列表与聊天页操作条是同一个闸门的两个入口，
// 各自写死一份必然漂移（曾经就是列表页放宽到含 springboot、聊天页还停在 vue3-fastapi）。
export const PUBLISHABLE_STACKS = ['vue3-fastapi', 'vue3-springboot']

export const canPublish = (app) => PUBLISHABLE_STACKS.includes(app?.stack)
