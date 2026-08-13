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
import { waitForDesktopEngine } from './desktop'

const components = [
  ElAlert, ElAside, ElBadge, ElButton, ElCard, ElCol, ElCollapse,
  ElCollapseItem, ElContainer, ElDescriptions, ElDescriptionsItem,
  ElDialog, ElDrawer, ElEmpty, ElForm, ElFormItem, ElHeader, ElInput,
  ElInputNumber, ElLoading, ElMain, ElMenu, ElMenuItem, ElOption,
  ElProgress, ElRadio, ElRadioGroup, ElRow, ElSelect, ElStep, ElSteps,
  ElSwitch, ElTable, ElTableColumn, ElTag, ElTimeline, ElTimelineItem,
  ElTooltip, ElUpload
]

async function bootstrap() {
  try {
    await waitForDesktopEngine()
  } catch (error) {
    document.querySelector('#app').innerHTML = `
      <main style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:48px;color:#1d2939">
        <h1 style="margin:0 0 12px;font-size:22px">C2B 本地分析引擎未启动</h1>
        <p style="line-height:1.7;color:#52606d">${String(error?.message || error)}</p>
        <p style="line-height:1.7;color:#52606d">请完全退出后重新打开 C2B Desktop；开发环境请确认 Python Core 已安装。</p>
      </main>`
    return
  }
  const app = createApp(App)
  components.forEach(component => app.use(component))
  provideGlobalConfig({ locale: zhCn }, app, true)
  app.use(router).mount('#app')
}

bootstrap()
