<template>
  <div v-loading="loading">
    <el-empty v-if="!pid" description="还没有选择项目">
      <template #description>
        <div class="empty-title">欢迎使用 Code2Business 业务分析平台</div>
        <div class="empty-desc">上传旧系统代码，自动还原业务流程、业务规则与模块需求规格</div>
      </template>
      <el-button type="primary" size="large" @click="$emit('create-project')">
        新建项目（上传代码）
      </el-button>
    </el-empty>
    <template v-else>
    <GuideBanner v-if="pipelineNext" :title="`下一步：${pipelineNext.label}`"
                  :desc="guideDesc"
                  :status-text="guideStatus"
                  :status-type="guideStatusType"
                  :action-text="guideAction"
                  @action="goGuide" />
    <el-row :gutter="16">
      <el-col :span="6" v-for="card in cards" :key="card.label">
        <el-card shadow="hover">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value">{{ card.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="mt16" shadow="never">
      <template #header>系统全景</template>
      <el-row :gutter="24">
        <el-col :span="8">
          <div class="sec-label">代码规模</div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="代码行数">{{ fmt(scale.total_lines) }}</el-descriptions-item>
            <el-descriptions-item label="代码文件">{{ fmt(scale.total_files) }}</el-descriptions-item>
            <el-descriptions-item label="已排除产物">{{ fmt(scale.excluded_files) }}</el-descriptions-item>
            <el-descriptions-item label="疑似历史版本">
              {{ fmt(scale.version_files) }} 文件 / {{ fmt(scale.version_lines) }} 行
            </el-descriptions-item>
          </el-descriptions>
        </el-col>
        <el-col :span="8">
          <div class="sec-label">语言分布（行数）</div>
          <div v-for="(n, ext) in scale.lines_by_ext || {}" :key="ext"
               class="lang-row">
            <span class="lang-ext">{{ ext }}</span>
            <el-progress :percentage="langPct(n)" :stroke-width="10"
                         :show-text="false" class="lang-bar" />
            <span class="lang-num">{{ fmt(n) }}</span>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="sec-label">编码分布（抽样）</div>
          <div v-for="(n, enc) in scale.encoding_samples || {}" :key="enc"
               class="lang-row">
            <span class="lang-ext">{{ enc }}</span>
            <el-progress :percentage="langPct(n)" :stroke-width="10"
                         :show-text="false" class="lang-bar" />
            <span class="lang-num">{{ n }}</span>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-card class="mt16" shadow="never" v-if="stats">
      <template #header>图谱统计</template>
      <el-row :gutter="16">
        <el-col :span="6" v-for="(v, k) in stats" :key="k">
          <div class="stat-label">{{ statLabels[k] || k }}</div>
          <div class="stat-value">{{ fmt(v) }}</div>
        </el-col>
      </el-row>
    </el-card>

    <el-row :gutter="16" class="mt16" v-if="detail">
      <el-col :span="8">
        <el-card shadow="never" class="full-card">
          <template #header>模块规模 Top 10</template>
          <div v-for="m in detail.top_modules" :key="m.name" class="rank-row">
            <span class="rank-name">{{ m.name }}</span>
            <el-progress :percentage="modPct(m.lines)" :stroke-width="8"
                         :show-text="false" class="rank-bar" />
            <span class="rank-num">{{ fmt(m.lines) }} 行</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="full-card">
          <template #header>规则类型分布</template>
          <div v-for="(n, t) in detail.rule_types" :key="t" class="rank-row">
            <span class="rank-name">{{ ruleTypeLabel(t) }}</span>
            <el-progress :percentage="ruleTypePct(n)" :stroke-width="8"
                         :show-text="false" class="rank-bar" />
            <span class="rank-num">{{ n }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="full-card">
          <template #header>入口与可信度</template>
          <div class="sec-label">程序入口类型</div>
          <div v-for="(n, k) in detail.entry_dist" :key="k" class="rank-row">
            <span class="rank-name">{{ entryLabel(k) }}</span>
            <el-tag size="small">{{ n }}</el-tag>
          </div>
          <div class="sec-label mt16">规则可信度分布</div>
          <div v-for="(n, b) in detail.confidence_buckets" :key="b" class="rank-row">
            <span class="rank-name">{{ b }}</span>
            <el-tag size="small" :type="b.startsWith('high') ? 'success'
                    : b.startsWith('mid') ? 'warning' : 'danger'">{{ n }}</el-tag>
          </div>
          <div class="scan-time" v-if="detail.scan_finished">
            最近扫描：{{ detail.scan_finished.slice(0, 19).replace('T', ' ') }}
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="mt16" shadow="never" v-if="domains.length">
      <template #header>业务域（{{ domains.length }}）</template>
      <el-tag v-for="d in domains" :key="d.id" class="mr8" type="success">
        {{ d.name }}
      </el-tag>
    </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import GuideBanner from '../components/GuideBanner.vue'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const emit = defineEmits(['create-project', 'start-scan'])
const router = useRouter()
const loading = ref(false)
const overview = ref(null)
const domains = ref([])
const pipelineNext = ref(null)
let loadToken = 0
let refreshTimer = null

const statLabels = {
  modules: '模块', classes: '类', methods: '方法', call_edges: '调用边',
  api_endpoints: '接口', data_tables: '数据表', sql_operations: 'SQL 操作',
  rules: '规则卡片', objects: '业务对象', flows: '业务流程', domains: '业务域',
  chain: '业务推演', confirmed_rules: '已确认规则', questions: '待确认项',
  parsed_files: '解析文件'
}
const ruleTypeLabel = t => ({ validation: '校验', calculation: '计算',
  status: '状态', permission: '权限', trigger: '自动触发',
  exception: '异常', other: '其他' }[t] || t)
const entryLabel = k => ({ batch: '批处理', api: 'API', schedule: '定时',
  mq: '消息', gui: 'GUI', other: '其他' }[k] || k)

const detail = computed(() => overview.value?.detail || null)
const modPct = n => {
  const top = Math.max(...(detail.value?.top_modules || []).map(m => m.lines), 1)
  return Math.round(n / top * 100)
}
const ruleTypePct = n => {
  const total = Object.values(detail.value?.rule_types || {}).reduce((a, b) => a + b, 0)
  return total ? Math.round(n / total * 100) : 0
}

const guideMap = {
  scan: { desc: '代码已经导入。确认来源无误后，开始构建模块、类、调用关系和 SQL 图谱。',
          status: '等待开始扫描', type: 'primary', action: '开始扫描' },
  label: { desc: '扫描已完成。点击「运行标注」让 AI 识别业务域、业务对象与规则卡片（375+ 条规则，耗时取决于代码量）。',
           status: '等待 AI 标注', type: 'warning', action: '运行标注' },
  graph: { desc: '标注完成，业务图谱已生成。到「业务对象与流程」「业务推演」查看识别结果。',
           status: '业务图谱已生成', type: 'success', action: '查看业务图谱', page: '/business' },
  confirm: { desc: '规则卡片已生成。到「流程与规则」页逐条确认，确认后的规则才会进入交付文档。',
             status: '等待人工确认', type: 'warning', action: '去确认规则', page: '/rules' },
  export: { desc: '所有步骤已完成。到「交付物中心」生成并下载交付文档与模块需求规格。',
            status: '流程已完成', type: 'success', action: '导出交付文档', page: '/documents' }
}

const guideDesc = computed(() => {
  if (pipelineNext.value?.status === 'running') {
    const task = pipelineNext.value
    const progress = task.total ? `（${task.progress || 0}/${task.total}）` : ''
    return `${task.label}正在进行：${task.stage || '准备中'}${progress}。页面会自动刷新，完成后展示分析结果。`
  }
  return guideMap[pipelineNext.value?.key]?.desc || ''
})
const guideStatus = computed(() => pipelineNext.value?.status === 'running'
  ? '任务进行中'
  : (guideMap[pipelineNext.value?.key]?.status || ''))
const guideStatusType = computed(() => pipelineNext.value?.status === 'running'
  ? 'primary'
  : (guideMap[pipelineNext.value?.key]?.type || 'info'))
const guideAction = computed(() => pipelineNext.value?.status === 'running'
  ? ''
  : (guideMap[pipelineNext.value?.key]?.action || ''))

function goGuide() {
  const g = guideMap[pipelineNext.value?.key]
  if (!g) return
  if (g.page) {
    router.push(g.page)
  } else if (g.action === '开始扫描') {
    emit('start-scan')
  } else if (g.action === '运行标注') {
    window.dispatchEvent(new CustomEvent('request-label'))
  }
}

const scale = computed(() => overview.value?.scale || {})
const fmt = n => (n ?? 0).toLocaleString()
const langPct = n => {
  const total = Object.values(scale.value.lines_by_ext || {}).reduce((a, b) => a + b, 0)
  return total ? Math.round((n || 0) / total * 100) : 0
}

const cards = computed(() => {
  const scale = overview.value?.scale || {}
  const stats = overview.value?.stats || {}
  return [
    { label: '总代码行数', value: (scale.total_lines || 0).toLocaleString() },
    { label: '代码文件', value: (scale.total_files || 0).toLocaleString() },
    { label: '规则卡片', value: (stats.rules || 0).toLocaleString() },
    { label: '业务对象', value: (stats.objects || 0).toLocaleString() },
    { label: '业务流程', value: (stats.flows || 0).toLocaleString() },
    { label: '待确认项', value: (stats.questions || 0).toLocaleString() }
  ]
})
const stats = computed(() => overview.value?.stats || null)

function syncRefreshTimer() {
  const running = pipelineNext.value?.status === 'running'
  if (running && !refreshTimer) {
    refreshTimer = setInterval(load, 5000)
  } else if (!running && refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

async function load() {
  const projectId = props.pid
  const token = ++loadToken
  if (!projectId) {
    overview.value = null
    domains.value = []
    pipelineNext.value = null
    syncRefreshTimer()
    loading.value = false
    return
  }
  loading.value = true
  try {
    // 流程状态在导入/扫描尚未完成时仍然可用；概览和业务域依赖已完成扫描，
    // 因此不能让它们的 404 把进度提示一起吞掉。
    const pipelineResp = await api.get(`/projects/${projectId}/pipeline`)
    if (token !== loadToken || projectId !== props.pid) return
    pipelineNext.value = pipelineResp.data.next
    syncRefreshTimer()

    const [overviewResp, domainsResp] = await Promise.allSettled([
      api.get(`/projects/${projectId}/overview`),
      api.get(`/projects/${projectId}/domains`)
    ])
    if (token !== loadToken || projectId !== props.pid) return
    overview.value = overviewResp.status === 'fulfilled' ? overviewResp.value.data : null
    domains.value = domainsResp.status === 'fulfilled' ? domainsResp.value.data : []
  } finally {
    if (token === loadToken) {
      loading.value = false
    }
  }
}

function handleProjectChanged(event) {
  if (String(event?.detail) === String(props.pid)) {
    load()
  }
}

watch(() => props.pid, load)

onMounted(() => {
  load()
  window.addEventListener('project-changed', handleProjectChanged)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  window.removeEventListener('project-changed', handleProjectChanged)
})
</script>

<style scoped>
.stat-label { color: #6b7280; font-size: 13px; }
.stat-value { font-size: 22px; font-weight: 700; margin-top: 4px; }
.sec-label { font-weight: 600; color: #374151; margin-bottom: 10px; font-size: 13px; }
.lang-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.lang-ext { width: 80px; font-size: 12px; color: #4b5563; flex-shrink: 0; }
.lang-bar { flex: 1; }
.lang-num { width: 60px; text-align: right; font-size: 12px; color: #6b7280; }
.rank-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.rank-name { width: 150px; font-size: 12px; color: #4b5563; flex-shrink: 0;
             overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rank-bar { flex: 1; }
.rank-num { width: 66px; text-align: right; font-size: 12px; color: #6b7280;
            flex-shrink: 0; }
.scan-time { margin-top: 12px; color: #9ca3af; font-size: 12px; }
.full-card { height: 100%; }
.mt16 { margin-top: 16px; }
.mr8 { margin-right: 8px; }
.empty-title { font-size: 16px; font-weight: 700; margin-bottom: 8px; }
.empty-desc { color: #6b7280; font-size: 13px; }
</style>
