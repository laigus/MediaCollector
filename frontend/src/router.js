import { createRouter, createWebHashHistory } from 'vue-router'
import Dashboard from './views/Dashboard.vue'
import NewTask from './views/NewTask.vue'
import Settings from './views/Settings.vue'

const routes = [
  { path: '/', name: 'Dashboard', component: Dashboard },
  { path: '/new-task', name: 'NewTask', component: NewTask },
  { path: '/settings', name: 'Settings', component: Settings },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
