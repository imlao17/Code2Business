<template>
  <div v-loading="loading">
    <el-alert v-if="!chain.has_chain" :title="chain.message || '暂无数据'"
              type="info" :closable="false" />
    <template v-else>
      <el-row :gutter="16">
        <el-col :span="16">
          <el-card shadow="never">
            <template #header>业务驱动关系图（对象间驱动/依赖）</template>
            <div ref="chartEl" class="chart"></div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never">
            <template #header>业务主线（从起点到终点的业务驱动顺序）</template>
            <el-steps direction="vertical" :active="chain.mainline.length">
              <el-step v-for="(m, i) in chain.mainline" :key="i" :title="m"
                       :description="'第 ' + (i + 1) + ' 阶段'" />
            </el-steps>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="mt16">
        <template #header>产品设计逻辑（当初为什么这么设计）</template>
        <p class="narrative">{{ chain.design_narrative }}</p>
        <el-tag size="small" type="info">可信度 {{ chain.confidence }}%</el-tag>
        <el-tag size="small" class="ml8" type="warning">模型 {{ chain.model }}</el-tag>
        <el-tooltip content="绕过缓存重新调用 AI 推演（30-90 秒）" placement="top">
          <el-button size="small" class="ml8" :loading="rerunning"
                     @click="rerun">强制重新推演</el-button>
        </el-tooltip>
      </el-card>

      <el-row :gutter="16" class="mt16">
        <el-col :span="14">
          <el-card shadow="never">
            <template #header>驱动关系明细（{{ (chain.relationships || []).length }}）</template>
            <el-table :data="chain.relationships" size="small">
              <el-table-column prop="from" label="来源对象" width="130" />
              <el-table-column prop="relation" label="驱动关系" width="100" />
              <el-table-column prop="to" label="目标对象" width="130" />
              <el-table-column label="证据核实" width="110">
                <template #default="{ row }">
                  <el-tag size="small" :type="verifyType(row.verification)">
                    {{ row.verification }} {{ row.verification_label }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="evidence" label="证据" min-width="200"
                               show-overflow-tooltip />
            </el-table>
            <div class="verify-tip">
              核实等级：V4 多源证据 / V3 表关联 / V2 部分证据 / V1 单一弱证据 ——
              无证据的关系已移入「推断假设」，不参与主线结论（衡木理念：证据决定可信）
            </div>
          </el-card>
        </el-col>
        <el-col :span="10">
          <el-card shadow="never">
            <template #header>推断假设（待确认）</template>
            <el-empty v-if="!chain.assumptions.length" description="无" />
            <ul class="assumptions">
              <li v-for="(a, i) in chain.assumptions" :key="i">{{ a }}</li>
            </ul>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import api, { LONG_REQUEST_TIMEOUT } from '../api'

echarts.use([GraphChart, LegendComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({ pid: { type: [Number, String], default: null } })
const createEmptyChain = () => ({
  has_chain: false,
  mainline: [],
  relationships: [],
  design_narrative: '',
  assumptions: []
})
const chain = ref(createEmptyChain())
const chartEl = ref(null)
let chart = null
let loadToken = 0

function disposeChart() {
  if (chart) {
    chart.dispose()
    chart = null
  }
}

async function load() {
  const projectId = props.pid
  const token = ++loadToken
  if (!projectId) {
    chain.value = createEmptyChain()
    disposeChart()
    loading.value = false
    return
  }
  loading.value = true
  try {
    chain.value = createEmptyChain()
    disposeChart()
    const data = (await api.get(`/projects/${projectId}/chain`)).data
    if (token !== loadToken || projectId !== props.pid) return
    chain.value = data
    if (chain.value.has_chain) {
      await nextTick()
      renderChart()
    } else {
      disposeChart()
    }
  } finally {
    if (token === loadToken) {
      loading.value = false
    }
  }
}

function renderChart() {
  const rels = chain.value.relationships || []
  const mainline = chain.value.mainline || []
  const nodes = [...new Set([...mainline, ...rels.flatMap(r => [r.from, r.to])])]
  const nodeData = nodes.map(n => ({
    name: n,
    category: mainline.indexOf(n) >= 0 ? '主线' : '关联',
    symbolSize: mainline.indexOf(n) >= 0 ? 48 : 36,
    label: { fontSize: 13, fontWeight: 'bold' }
  }))
  if (!chart) {
    chart = echarts.init(chartEl.value)
  }
  chart.setOption({
    tooltip: { formatter: p => p.dataType === 'edge'
      ? `${p.data.source} → ${p.data.target}<br/>${p.data.relation || ''}` : p.data.name },
    legend: { data: ['主线', '关联'] },
    series: [{
      type: 'graph', layout: 'force', roam: true,
      force: { repulsion: 200, edgeLength: [60, 160], gravity: 0.08 },
      label: { show: true },
      edgeLabel: { show: true, formatter: p => p.data.relation || '',
                   fontSize: 10, color: '#64748b' },
      data: nodeData,
      links: rels.map(r => ({
        source: r.from, target: r.to, relation: r.relation,
        lineStyle: { width: 2, curveness: 0.1,
                     color: { V4: '#67c23a', V3: '#67c23a', V2: '#e6a23c',
                              V1: '#e6a23c', V0: '#f56c6c' }[r.verification] || '#909399' }
      })),
      categories: [{ name: '主线' }, { name: '关联' }]
    }]
  }, true)
}

const loading = ref(false)
const rerunning = ref(false)
const verifyType = v => ({ V4: 'success', V3: 'success', V2: 'warning',
                           V1: 'warning', V0: 'danger' }[v] || 'info')
const handleResize = () => chart?.resize()

async function rerun() {
  if (!props.pid) return
  rerunning.value = true
  try {
    const r = (await api.post(`/projects/${props.pid}/chain/rerun`, null, {
      timeout: LONG_REQUEST_TIMEOUT
    })).data
    if (r.ok) {
      ElMessage.success('重新推演完成')
      load()
    } else {
      ElMessage.error(r.error || '推演失败')
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || String(e))
  } finally {
    rerunning.value = false
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
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('project-changed', handleProjectChanged)
  window.removeEventListener('resize', handleResize)
  disposeChart()
})
</script>

<style scoped>
.chart { height: 480px; }
.narrative { line-height: 1.9; color: #374151; font-size: 14px; }
.assumptions { color: #92400e; line-height: 1.8; padding-left: 18px; }
.verify-tip { margin-top: 10px; color: #6b7280; font-size: 12px; }
.mt16 { margin-top: 16px; }
.ml8 { margin-left: 8px; }
</style>
