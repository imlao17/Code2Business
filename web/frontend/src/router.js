import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/overview' },
  { path: '/overview', component: () => import('./views/Overview.vue'), meta: { title: '项目总览' } },
  { path: '/module-specs', component: () => import('./views/ModuleSpecs.vue'), meta: { title: '模块需求规格' } },
  { path: '/business', component: () => import('./views/Business.vue'), meta: { title: '业务对象与流程' } },
  { path: '/chain', component: () => import('./views/Chain.vue'), meta: { title: '业务推演' } },
  { path: '/rules', component: () => import('./views/Rules.vue'), meta: { title: '流程与规则' } },
  { path: '/data', component: () => import('./views/Data.vue'), meta: { title: '数据与接口' } },
  { path: '/questions', component: () => import('./views/Questions.vue'), meta: { title: '待确认项' } },
  { path: '/documents', component: () => import('./views/Documents.vue'), meta: { title: '文档与交付物' } }
]

export default createRouter({ history: createWebHistory(), routes })
