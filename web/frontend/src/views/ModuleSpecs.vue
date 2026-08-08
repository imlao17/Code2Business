<template>
  <div>
    <GuideBanner title="模块需求规格 —— 新系统重建的业务基线"
                  :desc="'从代码图谱还原每个模块的需求规格（功能点/流程/数据/规则/信息缺口），'
                    + '全部模块分析完成后可打包下载，作为新系统重建的需求文档。'
                    + '先点「批量分析全部模块」生成所有规格，再逐模块查看确认。'"
                  :status-text="`已分析 ${analyzedCount}/${modules.length} 个模块`"
                  :status-type="analyzedCount === modules.length && modules.length ? 'success' : 'warning'"
                  :extra-text="analyzedCount === modules.length && modules.length ? '全部完成，可打包下载' : '未分析模块可单独点击「分析」'"
                  :action-text="analyzedCount === modules.length && modules.length ? '打包下载' : '批量分析全部模块'"
                  @action="analyzedCount === modules.length && modules.length ? downloadZip() : startBatch()" />
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="search" placeholder="搜索模块名称（如 Sms01206、common）"
                  clearable class="search-input" :prefix-icon="'Search'">
        </el-input>
        <span class="count">共 {{ filteredModules.length }} 个模块</span>
        <span class="toolbar-spacer"></span>
        <el-button type="primary" :loading="batchRunning" @click="startBatch">
          批量分析全部模块（{{ analyzedCount }}/{{ modules.length }} 已分析）
        </el-button>
        <el-button :disabled="analyzedCount === 0" @click="downloadZip">
          打包下载全部需求规格
        </el-button>
        <el-progress v-if="batchRunning" class="progress"
                     :percentage="batchPercent" :stroke-width="14"
                     :status="batchStatus" />
        <span v-if="batchRunning && jobStage" class="stage">{{ jobStage }}</span>
      </div>
    </el-card>

    <el-card shadow="never" class="mt16">
      <el-table :data="filteredModules" size="small" height="600"
                :row-class-name="rowClass">
        <el-table-column prop="name" label="模块" min-width="260" />
        <el-table-column prop="lines" label="行数" width="110" sortable />
        <el-table-column prop="file_count" label="文件数" width="80" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="row.analyzed ? 'success' : 'info'">
              {{ row.analyzed ? '已分析' : '未分析' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260">
          <template #default="{ row }">
            <el-button size="small" type="primary" text
                       :loading="analyzingId === row.module_id"
                       @click="analyzeOne(row, false)">
              {{ row.analyzed ? '重新分析' : '分析' }}
            </el-button>
            <el-tooltip content="绕过缓存，完全重新调用 AI 生成（耗时较长）" placement="top">
              <el-button size="small" type="warning" text
                         :loading="analyzingId === row.module_id"
                         @click="analyzeOne(row, true)">
                强制重新分析
              </el-button>
            </el-tooltip>
            <el-button size="small" type="primary" text @click="viewModule(row)"
                       :disabled="!row.analyzed">查看</el-button>
            <el-button size="small" text :loading="downloadId === row.module_id"
                       @click="downloadOne(row)"
                       :disabled="!row.analyzed">下载</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="viewOpen" size="62%" :title="current?.name + ' 需求规格'">
      <div v-loading="viewLoading" style="min-height: 300px"></div>
      <template v-if="analysis && !viewLoading">
        <div class="mb12">
          <el-button type="primary" size="small" @click="downloadOne(current)">
            下载此模块需求规格
          </el-button>
        </div>
        <SpecViewer :analysis="analysis" />
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import api, { LONG_REQUEST_TIMEOUT } from '../api'
import SpecViewer from '../components/SpecViewer.vue'
import GuideBanner from '../components/GuideBanner.vue'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const modules = ref([])
const batchRunning = ref(false)
const jobStage = ref('')
const batchStatus = ref('')
const batchPercent = ref(0)
const viewOpen = ref(false)
const viewLoading = ref(false)
const analysis = ref(null)
const current = ref(null)
const analyzingId = ref(null)
const downloadId = ref(null)
const search = ref('')
const analysisCache = reactive({})
let pollTimer = null
let loadToken = 0
let viewToken = 0

function clearAnalysisCache() {
  Object.keys(analysisCache).forEach(key => delete analysisCache[key])
}

const createEmptyState = () => {
  viewToken += 1
  modules.value = []
  batchRunning.value = false
  jobStage.value = ''
  batchStatus.value = ''
  batchPercent.value = 0
  viewOpen.value = false
  viewLoading.value = false
  analysis.value = null
  current.value = null
  analyzingId.value = null
  downloadId.value = null
  clearAnalysisCache()
}

function stopBatchPoll(resetRunning = false) {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  if (resetRunning) {
    batchRunning.value = false
    jobStage.value = ''
  }
}

const filteredModules = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return modules.value
  return modules.value.filter(m =>
    m.name.toLowerCase().includes(q) ||
    String(m.lines).includes(q))
})

async function analyzeOne(row, force = false) {
  if (!props.pid) return
  analyzingId.value = row.module_id
  try {
    const r = (await api.post(`/projects/${props.pid}/modules/${row.module_id}/analyze`,
      null, {
        params: { force: force || undefined },
        timeout: LONG_REQUEST_TIMEOUT
      })).data
    if (!r.ok) {
      ElMessage.error(r.error || '分析失败')
    } else {
      delete analysisCache[row.module_id]
      ElMessage.success(`「${row.name}」分析完成`
        + (r.cached ? '（命中缓存）' : force ? '（已强制重新生成）' : ''))
      if (viewOpen.value && current.value?.module_id === row.module_id) {
        await viewModule(row)
      }
    }
    load()
  } finally {
    analyzingId.value = null
  }
}

const analyzedCount = computed(() =>
  modules.value.filter(m => m.analyzed).length)

async function load() {
  const projectId = props.pid
  const token = ++loadToken
  if (!projectId) {
    createEmptyState()
    return
  }
  const data = (await api.get(`/projects/${projectId}/modules/analyses`)).data
  if (token !== loadToken || projectId !== props.pid) return
  modules.value = data
}

async function startBatch() {
  if (!props.pid) return
  const projectId = props.pid
  stopBatchPoll(true)
  batchRunning.value = true
  batchPercent.value = 0
  batchStatus.value = ''
  jobStage.value = '任务启动中...'
  try {
    const r = (await api.post(`/projects/${projectId}/modules/analyze-all`)).data
    pollTimer = setInterval(poll, 4000)
    await poll()
    async function poll() {
      if (props.pid !== projectId) {
        stopBatchPoll(true)
        return
      }
      try {
        const j = (await api.get(`/projects/${projectId}/label-jobs/${r.job_id}`)).data
        if (j.status === 'done') {
          stopBatchPoll()
          batchRunning.value = false
          batchStatus.value = 'success'
          clearAnalysisCache()
          const failed = j.result?.failed || 0
          if (failed) {
            ElMessage.warning(
              `批量分析完成：${j.result?.analyzed || 0} 个可用，${failed} 个失败`
            )
          } else {
            ElMessage.success(`批量分析完成：${j.result?.analyzed || 0} 个模块`)
          }
          if (props.pid === projectId) {
            await load()
          }
        } else if (j.status === 'failed') {
          stopBatchPoll()
          batchRunning.value = false
          batchStatus.value = 'exception'
          ElMessage.error(j.error || '批量分析失败')
          if (props.pid === projectId) {
            clearAnalysisCache()
            await load()
          }
        } else {
          batchPercent.value = j.total ? Math.round(j.progress / j.total * 100) : 0
          jobStage.value = `${j.stage || ''}（${j.progress}/${j.total}）`
        }
      } catch {
        // 下轮轮询重试
      }
    }
  } catch (e) {
    batchRunning.value = false
    batchStatus.value = 'exception'
    jobStage.value = ''
    ElMessage.error(e?.response?.data?.detail || String(e))
  }
}

async function fetchModuleAnalysis(row) {
  const projectId = props.pid
  const moduleId = row?.module_id
  if (!projectId || !moduleId) return null
  if (analysisCache[moduleId]) return analysisCache[moduleId]
  const data = (await api.get(`/projects/${projectId}/modules/${moduleId}/analysis`)).data
  if (projectId !== props.pid) return null
  analysisCache[moduleId] = data
  return data
}

async function viewModule(row) {
  const token = ++viewToken
  current.value = row
  viewOpen.value = true
  viewLoading.value = true
  analysis.value = null
  try {
    const result = await fetchModuleAnalysis(row)
    if (token !== viewToken || current.value?.module_id !== row.module_id) return
    analysis.value = result
  } finally {
    if (token === viewToken) {
      viewLoading.value = false
    }
  }
}

async function downloadOne(row) {
  downloadId.value = row.module_id
  try {
    const moduleAnalysis = await fetchModuleAnalysis(row)
    const md = moduleAnalysis?.markdown
    const name = moduleAnalysis?.module_name || row.name
    const blob = new Blob([md || ''], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${name.replace(/[\\/]/g, '__')}-需求规格.md`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || String(e))
  } finally {
    downloadId.value = null
  }
}

async function downloadZip() {
  const r = await api.get(`/projects/${props.pid}/modules/analyses/export`, {
    responseType: 'blob'
  })
  const url = URL.createObjectURL(r.data)
  const a = document.createElement('a')
  a.href = url
  a.download = 'module-specs.zip'
  a.click()
  URL.revokeObjectURL(url)
}

const rowClass = ({ row }) => row.analyzed ? '' : 'row-pending'

function handleProjectChanged(event) {
  if (String(event?.detail) === String(props.pid)) {
    load()
  }
}

watch(() => props.pid, () => {
  stopBatchPoll(true)
  createEmptyState()
  load()
})

onMounted(() => {
  load()
  window.addEventListener('project-changed', handleProjectChanged)
})

onUnmounted(() => {
  stopBatchPoll(true)
  window.removeEventListener('project-changed', handleProjectChanged)
})
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.search-input { width: 280px; }
.count { color: #6b7280; font-size: 12px; }
.toolbar-spacer { flex: 1; }
.progress { width: 300px; }
.stage { color: #6b7280; font-size: 12px; }
.mt16 { margin-top: 16px; }
.mb8 { margin-bottom: 8px; }
.mr4 { margin-right: 4px; }
.mt4 { margin-top: 4px; }
:deep(.row-pending) { opacity: 0.55; }
</style>
