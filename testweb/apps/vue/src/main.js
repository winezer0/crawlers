import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './shell.vue'
import Home from './views/home.vue'
import Detail from './views/detail.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', component: Home }, { path: '/guide', component: Home }, { path: '/items/:id', component: Detail }],
})
createApp(App).use(router).mount('#app')
