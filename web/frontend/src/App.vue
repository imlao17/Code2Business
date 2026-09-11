<template>
  <el-container :class="['layout', { 'desktop-shell': isDesktop }]">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <span class="logo-mark">⌁</span>
        <span>Code2Business<small v-if="isDesktop">Desktop</small></span>
      </div>
      <el-menu :default-active="$route.path" router class="menu">
        <el-menu-item index="/overview">项目总览</el-menu-item>
        <el-menu-item index="/module-specs">模块需求规格</el-menu-item>
        <el-menu-item index="/business">业务对象与流程</el-menu-item>
        <el-menu-item index="/chain">业务推演</el-menu-item>
        <el-menu-item index="/rules">流程与规则</el-menu-item>
        <el-menu-item index="/data">数据与接口</el-menu-item>
        <el-menu-item index="/questions">待确认项</el-menu-item>
        <el-menu-item index="/documents">文档与交付物</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="title">{{ $route.meta.title }}</div>
        <div class="header-right">
          <span v-if="isDesktop" class="local-only">本地工作台 · 数据仅保存在此 Mac</span>
          <el-button size="small" type="warning" :loading="autoRunning"
                     :disabled="!pid" @click="runAuto">一键分析</el-button>
          <el-button size="small" type="primary" :loading="labeling"
                     :disabled="!pid" @click="runLabel">运行标注</el-button>
          <el-button size="small" type="success" @click="importOpen = true">新建项目</el-button>
          <el-tag size="small" :type="llm.mode === 'mock' ? 'warning' : 'success'" class="mr8">
            {{ llm.mode === 'mock' ? 'MOCK' : 'API' }}{{ llm.has_key ? '' : ' 未配Key' }}
          </el-tag>
          <el-button size="small" @click="helpOpen = true">帮助</el-button>
          <el-button size="small" @click="settingsOpen = true">LLM 设置</el-button>
          <el-select v-model="pid" placeholder="选择项目" style="width: 280px"
                     @change="onProject" filterable>
            <el-option v-for="p in projects" :key="p.id"
                       :label="`#${p.id} ${p.name}`" :value="p.id" />
          </el-select>
          <el-button size="small" type="danger" text :disabled="!pid"
                     @click="askDelete">删除项目</el-button>
        </div>
      </el-header>
      <div class="pipeline-bar" v-if="pid && pipeline.steps.length">
        <el-steps :active="activeStep" finish-status="success" align-center
                  class="pipeline-steps">
          <el-step v-for="(st, i) in pipeline.steps" :key="st.key"
                   :title="st.label"
                   :status="stepStatus(st)"
                   :description="stepDesc(st)">
            <template #icon>
              <span class="step-icon" @click="goStep(st)">{{ i + 1 }}</span>
            </template>
          </el-step>
        </el-steps>
        <div class="pipeline-next" v-if="pipeline.next">
          <span v-if="pipeline.next.status === 'pending'">
            下一步：{{ pipeline.next.label }}
          </span>
          <span v-else-if="pipeline.next.status === 'error'">
            {{ pipeline.next.error || `${pipeline.next.label}失败，请重试` }}
          </span>
          <span v-else-if="pipeline.next.status === 'running'">
            正在执行：{{ pipeline.next.label }}
          </span>
          <span v-else>流程已完成，可导出交付文档</span>
          <el-button v-if="pipeline.next.status !== 'running'" size="small" type="primary"
                     @click="goStep(pipeline.next)">
            {{ pipeline.next.status === 'error'
              ? (pipeline.next.key === 'scan' ? '重新扫描' : '重新导入')
              : '去执行' }}
          </el-button>
          <el-button v-else size="small" @click="routerPush('/overview')">查看进度</el-button>
        </div>
      </div>
      <el-main class="main">
        <router-view :pid="pid" :key="pid" @create-project="importOpen = true"
                     @start-scan="startScan" />
      </el-main>
      <QaFloat :pid="pid" />
      <HelpDrawer v-model="helpOpen" :llm="llm" />

      <el-dialog v-model="importOpen" title="新建项目（导入代码）" width="520px">
        <el-form label-width="90px">
          <el-form-item label="项目名称" required>
            <el-input v-model="importForm.name" placeholder="如：订单管理系统" />
          </el-form-item>
          <el-form-item label="代码来源">
            <el-radio-group v-model="importForm.source">
              <el-radio v-if="isDesktop" value="local">本地代码目录</el-radio>
              <el-radio value="zip">上传 Zip 包</el-radio>
              <el-radio value="git">Git 地址</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="importForm.source === 'local'" label="代码目录" required>
            <el-input v-model="importForm.localPath" readonly placeholder="选择本机代码目录">
              <template #append><el-button @click="chooseLocalDirectory">选择目录…</el-button></template>
            </el-input>
            <div class="form-hint">仅关联此目录，不会复制或删除你的原始代码。</div>
          </el-form-item>
          <el-form-item v-else-if="importForm.source === 'zip'" label="Zip 文件" required>
            <el-upload :auto-upload="false" :limit="1" drag
                       accept=".zip" :on-change="onZipChange">
              <div class="upload-tip">拖拽 zip 代码包到此处，或点击选择</div>
            </el-upload>
          </el-form-item>
          <el-form-item v-else label="Git 地址" required>
            <el-input v-model="importForm.gitUrl"
                      placeholder="https://github.com/xxx/xxx.git" />
          </el-form-item>
          <el-form-item v-if="importing">
            <el-progress :percentage="importPercent" :stroke-width="14"
                         :status="importStatus" />
            <div class="import-stage">{{ importStage }}</div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="importOpen = false">取消</el-button>
          <el-button type="primary" :loading="importing" @click="doImport">
            {{ importForm.source === 'local' ? '关联本地目录' : '导入代码' }}
          </el-button>
        </template>
      </el-dialog>

      <el-dialog v-model="settingsOpen" title="LLM 设置" width="480px">
        <el-alert type="info" :closable="false" class="llm-intro"
                  title="这一步是给「AI 分析」接一个大模型服务"
                  description="没有 Key 也可以先扫描、看图谱；配置后才能自动归纳业务规则和生成模块规格。
                  推荐 DeepSeek（便宜、国内直连）：注册后在官方后台创建 API Key 填到下面即可。" />
        <el-form label-width="90px">
          <el-form-item label="模式">
            <el-radio-group v-model="llmForm.mode">
              <el-radio value="api">API（云端）</el-radio>
              <el-radio value="mock">Mock（模拟演示）</el-radio>
            </el-radio-group>
            <div class="form-hint">Mock 用内置模拟数据演示流程，不做真实业务分析。</div>
          </el-form-item>
          <el-form-item v-if="llmForm.mode === 'api'" label="Provider">
            <el-select v-model="llmForm.provider" style="width: 100%">
              <el-option label="OpenAI Compatible（OpenAI / DeepSeek / 自定义兼容端）"
                         value="openai_compatible" />
              <el-option label="Anthropic Native（Claude 原生 API）" value="anthropic" />
            </el-select>
            <div class="form-hint">{{ providerHint }}</div>
          </el-form-item>
          <el-form-item label="API 地址">
            <el-input v-model="llmForm.base_url" :placeholder="providerUrlPlaceholder" />
          </el-form-item>
          <el-form-item label="API Key">
            <el-input v-model="llmForm.api_key" type="password" show-password
                      :placeholder="llm.has_key ? '已保存：' + llm.key_masked + '（留空则不修改）' : 'sk-...'" />
            <div v-if="isDesktop" class="form-hint">
              {{ llm.key_storage === 'macos_keychain'
                ? '密钥已保存在当前用户的 macOS 钥匙串中。'
                : '保存后密钥将进入当前用户的 macOS 钥匙串。' }}
            </div>
          </el-form-item>
          <el-form-item label="模型">
            <el-input v-model="llmForm.model" placeholder="deepseek-chat" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button :loading="testing" @click="testLlm">测试连接</el-button>
          <el-button type="primary" @click="saveLlm">保存</el-button>
        </template>
      </el-dialog>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, watch, computed, defineAsyncComponent } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { LONG_REQUEST_TIMEOUT } from './api'
import { chooseLocalSourceDirectory, isDesktop } from './desktop'
import { friendlyError } from './errors'
const QaFloat = defineAsyncComponent(() => import('./components/QaFloat.vue'))
const HelpDrawer = defineAsyncComponent(() => import('./components/HelpDrawer.vue'))

const routerPush = useRouter().push

const pid = ref(null)
const projects = ref([])
const settingsOpen = ref(false)
const helpOpen = ref(false)
const testing = ref(false)
const labeling = ref(false)
const autoRunning = ref(false)
const importOpen = ref(false)
const importing = ref(false)
const importPercent = ref(0)
const importStatus = ref('')
const importStage = ref('')
const importForm = reactive({
  name: '', source: isDesktop ? 'local' : 'zip', gitUrl: '', localPath: ''
})
let zipFile = null
let importTimer = null
let importContext = null
let autoTimer = null
let autoContext = null
const llm = reactive({
  provider: 'openai_compatible', mode: 'api', has_key: false,
  key_masked: '', key_storage: 'config_file'
})
const llmForm = reactive({ provider: 'openai_compatible', mode: 'api', base_url: '', api_key: '', model: '' })
const pipeline = ref({ steps: [], next: null })
const activeStep = ref(0)
let pipelineTimer = null
let labelTimer = null
let labelContext = null
let pipelineLoadToken = 0

const hasRunningPipeline = computed(() =>
  pipeline.value.steps.some(st => st.status === 'running'))
const providerUrlPlaceholder = computed(() => llmForm.provider === 'anthropic'
  ? 'https://api.anthropic.com'
  : 'https://api.deepseek.com/v1')
const providerHint = computed(() => llmForm.provider === 'anthropic'
  ? '使用 Claude 原生 Messages API；填写 Anthropic API Key。'
  : '适用于 OpenAI、DeepSeek、Kimi、Ollama 及遵循 /chat/completions 的自建服务。')

const stepStatus = st => ({
  done: 'success', running: 'process', pending: 'wait', ready: 'wait', error: 'error'
}[st.status] || 'wait')

const stepDesc = st => {
  if (st.status === 'running') {
    const t = st.total ? `${st.progress}/${st.total}` : '进行中'
    return `${st.stage || ''} ${t}`
  }
  if (st.count != null) return `${st.count} 项`
  if (st.status === 'done') return '已完成'
  if (st.status === 'ready') return '可执行'
  if (st.status === 'error') return st.error || '执行失败'
  return ''
}

async function goStep(st) {
  if (!st?.page) return
  if (st.status === 'running') return
  if (st.key === 'import' && st.status === 'error') {
    importOpen.value = true
    return
  }
  if (st.key === 'scan' && ['pending', 'error'].includes(st.status)) {
    await startScan()
    return
  }
  if (st.key === 'label' && st.status === 'pending') {
    ElMessage.info('点击顶部「一键分析」可自动完成 AI 归纳；也可以用「运行标注」只跑标注')
    return
  }
  routerPush(st.page)
}

async function startScan() {
  if (!pid.value) return
  try {
    const r = (await api.post(`/projects/${pid.value}/scan`)).data
    ElMessage.success(r.message || '扫描任务已启动')
    await loadPipeline()
    routerPush('/overview')
  } catch (e) {
    ElMessage.error(friendlyError(e, '扫描启动失败'))
  }
}

async function loadPipeline() {
  const projectId = pid.value
  const token = ++pipelineLoadToken
  if (!projectId) {
    pipeline.value = { steps: [], next: null }
    activeStep.value = 0
    return
  }
  try {
    const d = (await api.get(`/projects/${projectId}/pipeline`)).data
    if (token !== pipelineLoadToken || projectId !== pid.value) return
    pipeline.value = d
    const runningIdx = d.steps.findIndex(st => st.status === 'running')
    activeStep.value = runningIdx >= 0 ? runningIdx : d.steps.length - 1
  } catch {
    if (token === pipelineLoadToken && projectId === pid.value) {
      pipeline.value = { steps: [], next: null }
      activeStep.value = 0
    }
  }
}

function startPipelinePoll() {
  stopPipelinePoll()
  pipelineTimer = setInterval(() => {
    if (pid.value && (labeling.value || hasRunningPipeline.value)) {
      loadPipeline()
    }
  }, 5000)
}

function stopPipelinePoll() {
  if (pipelineTimer) {
    clearInterval(pipelineTimer)
    pipelineTimer = null
  }
}

function stopImportPoll(resetState = false) {
  if (importTimer) {
    clearInterval(importTimer)
    importTimer = null
  }
  importContext = null
  if (resetState) {
    importing.value = false
  }
}

function stopLabelPoll(resetState = false) {
  if (labelTimer) {
    clearInterval(labelTimer)
    labelTimer = null
  }
  labelContext = null
  if (resetState) {
    labeling.value = false
  }
}

function stopAutoPoll(resetState = false) {
  if (autoTimer) {
    clearInterval(autoTimer)
    autoTimer = null
  }
  autoContext = null
  if (resetState) {
    autoRunning.value = false
  }
}

async function runAuto() {
  if (!pid.value) return
  const projectId = pid.value
  stopAutoPoll()
  autoRunning.value = true
  autoContext = { projectId }
  try {
    const r = (await api.post(`/projects/${projectId}/auto-run`, { run_ai: true })).data
    if (!r.ai_ready) {
      ElMessage.info('尚未配置 AI 服务：本次将只完成扫描与图谱，配置 Key 后可继续自动标注')
    } else {
      ElMessage.success('一键分析已启动：扫描 → AI 归纳 → 模块分析，可离开页面，进度见流程条')
    }
    await loadPipeline()
    routerPush('/overview')
    autoTimer = setInterval(async () => {
      if (!autoContext || autoContext.projectId !== projectId) return
      try {
        const j = (await api.get(`/projects/${projectId}/label-jobs/${r.job_id}`)).data
        if (j.status === 'done') {
          stopAutoPoll()
          const res = j.result || {}
          const summary = res.ai_ran
            ? `规则 ${res.label?.rules || 0} 条 / 对象 ${res.label?.objects || 0} / 模块规格 ${res.modules?.analyzed || 0}`
            : '已完成确定性扫描与图谱'
          ElMessage.success(`一键分析完成（${summary}）`)
          if (res.notes?.length) {
            ElMessage.info({ message: res.notes[0], duration: 8000 })
          }
          ElMessage.info('现在可以到「文档与验收」下载草稿版《客户交付总报告》；确认规则后可出正式版')
          if (pid.value === projectId) {
            window.dispatchEvent(new CustomEvent('project-changed', { detail: projectId }))
            loadPipeline()
          }
        } else if (j.status === 'failed') {
          stopAutoPoll()
          ElMessageBox.alert(friendlyError({ message: j.error }, '一键分析失败'), '一键分析失败')
        }
      } catch {
        // 轮询失败忽略，下轮重试
      }
    }, 3000)
  } catch (e) {
    stopAutoPoll()
    ElMessage.error(friendlyError(e, '一键分析启动失败'))
  }
}

function onZipChange(file) {
  zipFile = file.raw
}

async function chooseLocalDirectory() {
  try {
    const selected = await chooseLocalSourceDirectory()
    if (!selected) return
    importForm.localPath = selected.path
    if (!importForm.name.trim()) importForm.name = selected.name
  } catch (error) {
    ElMessage.error(error?.message || '无法打开本地目录选择器')
  }
}

async function finishImport(projectId, originPid) {
  stopImportPoll()
  importing.value = false
  importPercent.value = 100
  importStatus.value = 'success'
  ElMessage.success('代码导入完成，可以开始扫描')
  importOpen.value = false
  await loadProjects()
  if (pid.value === originPid || !pid.value) {
    pid.value = projectId
    window.dispatchEvent(new CustomEvent('project-changed', { detail: projectId }))
  }
}

async function doImport() {
  if (!importForm.name.trim()) {
    ElMessage.warning('请输入项目名称')
    return
  }
  if (importForm.source === 'zip' && !zipFile) {
    ElMessage.warning('请选择 zip 文件')
    return
  }
  if (importForm.source === 'local' && !importForm.localPath) {
    ElMessage.warning('请选择本地代码目录')
    return
  }
  if (importForm.source === 'git' && !importForm.gitUrl.trim()) {
    ElMessage.warning('请输入 Git 地址')
    return
  }
  stopImportPoll()
  const originPid = pid.value
  importing.value = true
  importPercent.value = 2
  importStatus.value = ''
  importStage.value = importForm.source === 'local' ? '正在关联本地代码目录...' : '上传/导入代码...'
  try {
    let r
    if (importForm.source === 'local') {
      r = (await api.post('/projects/import-local', {
        name: importForm.name.trim(), source_path: importForm.localPath
      })).data
    } else {
      const fd = new FormData()
      fd.append('name', importForm.name.trim())
      if (importForm.source === 'zip') {
        fd.append('file', zipFile)
      } else {
        fd.append('git_url', importForm.gitUrl.trim())
      }
      // ZIP 解压在请求中完成；Git 克隆由导入任务异步执行。
      r = (await api.post('/projects/import', fd, { timeout: 600000 })).data
    }
    if (r.error) {
      ElMessage.error(r.error)
      importing.value = false
      importStatus.value = 'exception'
      importStage.value = ''
      stopImportPoll()
      return
    }
    if (r.import_status === 'ready') {
      await finishImport(r.project_id, originPid)
      return
    }
    importContext = { projectId: r.project_id, jobId: r.job_id, originPid }
    importStage.value = '正在克隆 Git 仓库...'
    importTimer = setInterval(async () => {
      if (!importContext || importContext.jobId !== r.job_id) return
      try {
        const j = (await api.get(`/projects/${r.project_id}/label-jobs/${r.job_id}`)).data
        if (j.status === 'done') {
          await finishImport(r.project_id, originPid)
        } else if (j.status === 'failed') {
          stopImportPoll()
          importing.value = false
          importStatus.value = 'exception'
          ElMessage.error(j.error || '扫描失败')
        } else {
          const total = j.total || 100
          importPercent.value = Math.min(99, Math.round(j.progress / total * 100))
          importStage.value = `导入中：${j.stage || ''}（${j.progress}/${total}）`
        }
      } catch {
        // 轮询失败忽略，下轮重试
      }
    }, 3000)
  } catch (e) {
    stopImportPoll()
    importing.value = false
    importStatus.value = 'exception'
    importStage.value = ''
    ElMessage.error(friendlyError(e, '代码导入失败'))
  }
}

async function loadProjects() {
  projects.value = (await api.get('/projects')).data
  if (projects.value.length && !pid.value) {
    pid.value = projects.value[0].id
  }
}
async function loadLlm() {
  Object.assign(llm, (await api.get('/settings/llm')).data)
  llmForm.provider = llm.provider || 'openai_compatible'
  llmForm.mode = llm.mode
  llmForm.base_url = llm.base_url || providerUrlPlaceholder.value
  llmForm.model = llm.model || (llmForm.provider === 'anthropic' ? '' : 'deepseek-chat')
}
async function saveLlm() {
  const body = { provider: llmForm.provider, mode: llmForm.mode, base_url: llmForm.base_url, model: llmForm.model }
  if (llmForm.api_key) body.api_key = llmForm.api_key
  await api.post('/settings/llm', body)
  ElMessage.success('配置已保存')
  settingsOpen.value = false
  loadLlm()
}
async function testLlm() {
  testing.value = true
  try {
    const body = { provider: llmForm.provider, mode: llmForm.mode, base_url: llmForm.base_url, model: llmForm.model }
    if (llmForm.api_key) body.api_key = llmForm.api_key
    const r = (await api.post('/settings/llm/test', body, {
      timeout: LONG_REQUEST_TIMEOUT
    })).data
    if (r.ok) ElMessage.success(r.message)
    else ElMessage.error(r.message)
  } finally {
    testing.value = false
  }
}
async function runLabel() {
  if (!pid.value) return
  const projectId = pid.value
  stopLabelPoll()
  labeling.value = true
  labelContext = { projectId }
  try {
    const r = (await api.post(`/projects/${projectId}/label`, {
      limit: 100, concurrency: 16
    })).data
    if (r.error) {
      labeling.value = false
      labelContext = null
      ElMessageBox.alert(r.error, '标注失败')
      return
    }
    ElMessage.success('标注任务已启动，后台执行中')
    labelTimer = setInterval(async () => {
      if (!labelContext || labelContext.projectId !== projectId) return
      try {
        const j = (await api.get(`/projects/${projectId}/label-jobs/${r.job_id}`)).data
        if (j.status === 'done') {
          stopLabelPoll()
          labeling.value = false
          ElMessage.success(`标注完成：规则 ${j.result?.rules || 0} 条 / 对象 ${j.result?.objects || 0} / 流程 ${j.result?.flows || 0}`)
          if (pid.value === projectId) {
            window.dispatchEvent(new CustomEvent('project-changed', { detail: projectId }))
            loadPipeline()
          }
        } else if (j.status === 'failed') {
          stopLabelPoll()
          labeling.value = false
          ElMessageBox.alert(j.error || '标注失败', '标注失败')
        }
      } catch {
        // 轮询失败忽略，下轮重试
      }
    }, 3000)
  } catch (e) {
    stopLabelPoll()
    labeling.value = false
    ElMessageBox.alert(friendlyError(e, '标注失败'), '标注失败')
  }
}
async function askDelete() {
  if (!pid.value) return
  try {
    await ElMessageBox.confirm(
      `确定删除项目「${projects.value.find(p => p.id === pid.value)?.name}」？`
      + '将删除全部图谱数据和系统托管的代码目录，且不可恢复；外部代码目录会保留。',
      '删除项目', { type: 'warning', confirmButtonText: '删除',
                    confirmButtonClass: 'el-button--danger' })
  } catch {
    return  // 取消
  }
  try {
    const deletedPid = pid.value
    await api.delete(`/projects/${deletedPid}`)
    ElMessage.success('项目已删除')
    pid.value = null
    await loadProjects()
    if (projects.value.length) pid.value = projects.value[0].id
    window.dispatchEvent(new CustomEvent('project-changed', { detail: pid.value || deletedPid }))
  } catch (e) {
    ElMessage.error(friendlyError(e, '删除项目失败'))
  }
}

function onProject() {
  loadPipeline()
}

function handleRequestLabel() {
  runLabel()
}

function handleRequestAutoRun() {
  runAuto()
}

watch(pid, (nextPid, prevPid) => {
  if (nextPid !== prevPid) {
    if (labelContext) {
      stopLabelPoll(true)
    }
    if (importContext) {
      stopImportPoll(true)
    }
    if (autoContext) {
      stopAutoPoll(true)
    }
  }
  loadPipeline()
  startPipelinePoll()
}, { immediate: true })

onMounted(() => {
  loadProjects()
  loadLlm()
  window.addEventListener('request-label', handleRequestLabel)
  window.addEventListener('request-autorun', handleRequestAutoRun)
})

onUnmounted(() => {
  stopImportPoll(true)
  stopLabelPoll(true)
  stopAutoPoll(true)
  stopPipelinePoll()
  window.removeEventListener('request-label', handleRequestLabel)
  window.removeEventListener('request-autorun', handleRequestAutoRun)
})
</script>

<style>
html, body, #app { height: 100%; margin: 0; }
.layout { height: 100%; }
.aside { background: #1f2937; }
.logo { color: #fff; font-weight: 700; font-size: 17px; padding: 18px 20px; letter-spacing: .2px; display: flex; gap: 9px; align-items: center; }
.logo small { display: block; font-size: 10px; font-weight: 500; color: #94a3b8; letter-spacing: .4px; margin-top: 2px; }
.logo-mark { color: #67d4bd; font-size: 28px; line-height: 16px; font-weight: 400; }
.menu { border-right: none; background: transparent; }
.menu .el-menu-item { color: #cbd5e1; }
.menu .el-menu-item.is-active { color: #fff; background: #374151; }
.header { display: flex; align-items: center; justify-content: space-between;
          border-bottom: 1px solid #e5e7eb; background: #fff; }
.header-right { display: flex; align-items: center; gap: 8px; }
.title { font-size: 16px; font-weight: 600; }
.local-only { color: #64748b; font-size: 12px; margin-right: 4px; }
.pipeline-bar { background: #fff; border-bottom: 1px solid #e5e7eb;
                padding: 8px 20px 10px; }
.pipeline-steps { max-width: 900px; margin: 0 auto; }
.step-icon { cursor: pointer; font-size: 12px; }
.pipeline-next { text-align: center; margin-top: 6px; display: flex;
                 gap: 10px; justify-content: center; align-items: center;
                 color: #6b7280; font-size: 13px; }
.main { background: #f3f4f6; overflow: auto; }
.llm-intro { margin-bottom: 14px; }
.desktop-shell .aside { background: #17212d; }
.desktop-shell .menu .el-menu-item { margin: 3px 8px; border-radius: 7px; height: 38px; line-height: 38px; }
.desktop-shell .menu .el-menu-item.is-active { background: #314158; }
.desktop-shell .header { min-height: 54px; background: rgba(255, 255, 255, .94); }
.desktop-shell .main { background: #f6f7f9; }
@media (prefers-color-scheme: dark) {
  .desktop-shell .header, .desktop-shell .pipeline-bar { background: #202a36; border-color: #334155; color: #e2e8f0; }
  .desktop-shell .main { background: #18212c; }
  .desktop-shell .title { color: #f1f5f9; }
}
</style>
