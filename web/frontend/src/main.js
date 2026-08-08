import { createApp } from 'vue'
import {
  ElAlert,
  ElAside,
  ElBadge,
  ElButton,
  ElCard,
  ElCol,
  ElCollapse,
  ElCollapseItem,
  ElContainer,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDrawer,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElHeader,
  ElInput,
  ElInputNumber,
  ElLoading,
  ElMain,
  ElMenu,
  ElMenuItem,
  ElOption,
  ElProgress,
  ElRadio,
  ElRadioGroup,
  ElRow,
  ElSelect,
  ElStep,
  ElSteps,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTimeline,
  ElTimelineItem,
  ElTooltip,
  ElUpload,
  provideGlobalConfig
} from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import router from './router'

const app = createApp(App)
const components = [
  ElAlert, ElAside, ElBadge, ElButton, ElCard, ElCol, ElCollapse,
  ElCollapseItem, ElContainer, ElDescriptions, ElDescriptionsItem,
  ElDialog, ElDrawer, ElEmpty, ElForm, ElFormItem, ElHeader, ElInput,
  ElInputNumber, ElLoading, ElMain, ElMenu, ElMenuItem, ElOption,
  ElProgress, ElRadio, ElRadioGroup, ElRow, ElSelect, ElStep, ElSteps,
  ElSwitch, ElTable, ElTableColumn, ElTag, ElTimeline, ElTimelineItem,
  ElTooltip, ElUpload
]
components.forEach(component => app.use(component))
provideGlobalConfig({ locale: zhCn }, app, true)
app.use(router).mount('#app')
