<template>
  <div>
    <GuideBanner title="流程与规则 —— 业务规则的人工确认"
                  :desc="'AI 从代码中识别出业务规则卡片（含证据与可信度）。'
                    + '请逐条检查：点开卡片看代码证据，可信度高的「确认」，'
                    + '无业务价值的「废弃」，有疑问的记入待确认项。'
                    + '只有确认后的规则才会进入交付文档。'"
                  :status-text="`共 ${rules.length} 条规则`"
                  :status-type="confirmedCount > 0 ? 'success' : 'warning'"
                  :extra-text="`已确认 ${confirmedCount} 条（可多选后批量确认）`"
                  action-text="批量确认已选规则"
                  @action="batchConfirm" />
    <el-card shadow="never">
      <div class="filters">
        <el-select v-model="filter.type" placeholder="规则类型" clearable style="width: 140px">
          <el-option v-for="t in ruleTypes" :key="t.value" :label="t.label" :value="t.value" />
        </el-select>
        <el-select v-model="filter.state" placeholder="确认状态" clearable style="width: 140px">
          <el-option v-for="t in states" :key="t.value" :label="t.label" :value="t.value" />
        </el-select>
        <el-select v-model="filter.domain" placeholder="业务域" clearable style="width: 160px">
          <el-option v-for="d in domains" :key="d.id" :label="d.name" :value="d.id" />
        </el-select>
        <el-input-number v-model="filter.minConf" :min="0" :max="100" placeholder="最低可信度" />
        <el-button type="primary" @click="load">查询</el-button>
        <el-button type="success" :disabled="!selected.length" @click="batchConfirm">
          批量确认（{{ selected.length }}）
        </el-button>
        <el-button @click="exportExcel">导出已确认 Excel</el-button>
        <span class="count">共 {{ rules.length }} 条</span>
      </div>
    </el-card>

    <el-card shadow="never" class="mt16">
      <el-table :data="rules" size="small" @row-click="openDetail"
                @selection-change="sel => selected = sel.map(r => r.id)"
                :row-class-name="rowClass">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="name" label="规则名称" min-width="220" show-overflow-tooltip />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small">{{ typeLabel(row.rule_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="module" label="模块" width="200" show-overflow-tooltip />
        <el-table-column label="可信度" width="120">
          <template #default="{ row }">
            <el-progress :percentage="row.confidence" :stroke-width="10"
                         :color="confColor(row.confidence)" />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="stateType(row.state)">{{ stateLabel(row.state) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="evidence_file" label="证据文件" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-card>

    <el-drawer v-model="drawer" size="52%" :title="detail.name || '规则详情'">
      <div v-if="detail.id">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="类型">{{ typeLabel(detail.rule_type) }}</el-descriptions-item>
          <el-descriptions-item label="模块">{{ detail.module }}</el-descriptions-item>
          <el-descriptions-item label="触发方式">{{ detail.trigger || '-' }}</el-descriptions-item>
          <el-descriptions-item label="前置条件">{{ detail.pre_condition || '-' }}</el-descriptions-item>
          <el-descriptions-item label="处理逻辑">
            <pre class="pre">{{ detail.logic }}</pre>
          </el-descriptions-item>
          <el-descriptions-item label="状态变化">{{ detail.status_change || '-' }}</el-descriptions-item>
          <el-descriptions-item label="数据变化">{{ detail.data_change || '-' }}</el-descriptions-item>
          <el-descriptions-item label="后续动作">{{ detail.follow_up || '-' }}</el-descriptions-item>
          <el-descriptions-item label="异常处理">{{ detail.exception || '-' }}</el-descriptions-item>
          <el-descriptions-item label="影响范围">{{ detail.impact || '-' }}</el-descriptions-item>
          <el-descriptions-item label="证据">{{ detail.evidence_file }}:{{ (detail.evidence_lines || []).join('-') }}</el-descriptions-item>
          <el-descriptions-item label="可信度">
            <el-progress :percentage="detail.confidence" :stroke-width="12"
                         :color="confColor(detail.confidence)" />
          </el-descriptions-item>
        </el-descriptions>

        <div class="snippet-title">代码证据片段</div>
        <pre class="pre code">{{ detail.evidence_snippet }}</pre>

        <div class="actions">
          <el-button type="success" @click="confirm('confirm')">确认</el-button>
          <el-button type="warning" @click="openModify">修改</el-button>
          <el-button type="danger" @click="confirm('discard')">废弃</el-button>
          <el-input v-model="note" placeholder="备注（可选）" style="width: 240px" />
        </div>
      </div>
    </el-drawer>

    <el-dialog v-model="modifyOpen" title="修改规则" width="760px" destroy-on-close>
      <el-form label-width="96px">
        <el-form-item label="规则名称">
          <el-input v-model="modifyForm.name" />
        </el-form-item>
        <el-form-item label="规则类型">
          <el-select v-model="modifyForm.rule_type" style="width: 100%">
            <el-option v-for="t in ruleTypes" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="业务域">
          <el-select v-model="modifyForm.domain_id" clearable style="width: 100%">
            <el-option v-for="d in domains" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="模块">
          <el-input v-model="modifyForm.module" />
        </el-form-item>
        <el-form-item label="触发方式">
          <el-input v-model="modifyForm.trigger" />
        </el-form-item>
        <el-form-item label="前置条件">
          <el-input v-model="modifyForm.pre_condition" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="处理逻辑">
          <el-input v-model="modifyForm.logic" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="状态变化">
          <el-input v-model="modifyForm.status_change" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="数据变化">
          <el-input v-model="modifyForm.data_change" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="后续动作">
          <el-input v-model="modifyForm.follow_up" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="异常处理">
          <el-input v-model="modifyForm.exception" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="影响范围">
          <el-input v-model="modifyForm.impact" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="modifyOpen = false">取消</el-button>
        <el-button type="primary" :loading="modifying" @click="submitModify">保存修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import GuideBanner from '../components/GuideBanner.vue'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const rules = ref([])
const domains = ref([])
const detail = ref({})
const drawer = ref(false)
const note = ref('')
const selected = ref([])
const filter = reactive({ type: null, state: null, domain: null, minConf: null })
const modifyOpen = ref(false)
const modifying = ref(false)
const editableFields = [
  'name',
  'rule_type',
  'domain_id',
  'module',
  'trigger',
  'pre_condition',
  'logic',
  'status_change',
  'data_change',
  'follow_up',
  'exception',
  'impact'
]
const createModifyForm = () => ({
  name: '',
  rule_type: 'other',
  domain_id: null,
  module: '',
  trigger: '',
  pre_condition: '',
  logic: '',
  status_change: '',
  data_change: '',
  follow_up: '',
  exception: '',
  impact: ''
})
const modifyForm = reactive(createModifyForm())

const ruleTypes = [
  { value: 'validation', label: '校验' }, { value: 'calculation', label: '计算' },
  { value: 'status', label: '状态' }, { value: 'permission', label: '权限' },
  { value: 'trigger', label: '自动触发' }, { value: 'exception', label: '异常' },
  { value: 'other', label: '其他' }
]
const states = [
  { value: 'ai_labeled', label: 'AI 识别' }, { value: 'confirmed', label: '已确认' },
  { value: 'modified', label: '已修改' }, { value: 'discarded', label: '已废弃' },
  { value: 'conflict', label: '存在冲突' }
]
const typeLabel = v => (ruleTypes.find(t => t.value === v) || {}).label || v
const stateLabel = v => (states.find(t => t.value === v) || {}).label || v
const stateType = v => ({ ai_labeled: 'info', confirmed: 'success', modified: 'warning', discarded: 'danger' }[v] || 'info')
const confColor = c => c >= 75 ? '#67c23a' : c >= 50 ? '#e6a23c' : '#f56c6c'
const rowClass = ({ row }) => row.state === 'discarded' ? 'row-discarded' : ''
const confirmedCount = computed(() =>
  rules.value.filter(r => ['confirmed', 'modified'].includes(r.state)).length)

async function load() {
  if (!props.pid) {
    rules.value = []
    domains.value = []
    detail.value = {}
    drawer.value = false
    modifyOpen.value = false
    return
  }
  const params = {}
  if (filter.type) params.rule_type = filter.type
  if (filter.state) params.state = filter.state
  if (filter.domain) params.domain_id = filter.domain
  if (filter.minConf != null) params.min_confidence = filter.minConf
  rules.value = (await api.get(`/projects/${props.pid}/rules`, { params })).data
  domains.value = (await api.get(`/projects/${props.pid}/domains`)).data
}

async function openDetail(row) {
  detail.value = (await api.get(`/projects/${props.pid}/rules/${row.id}`)).data
  note.value = ''
  drawer.value = true
}

async function confirm(action) {
  await api.post(`/projects/${props.pid}/rules/${detail.value.id}/confirm`, {
    action, user: 'web-user', note: note.value
  })
  ElMessage.success(action === 'confirm' ? '已确认' : '已废弃')
  detail.value.state = action === 'confirm' ? 'confirmed' : 'discarded'
  load()
}

function openModify() {
  Object.assign(modifyForm, createModifyForm())
  for (const key of editableFields) {
    modifyForm[key] = detail.value[key] ?? createModifyForm()[key]
  }
  modifyOpen.value = true
}

async function submitModify() {
  const fields = {}
  for (const key of editableFields) {
    const nextValue = modifyForm[key] ?? null
    const currentValue = detail.value[key] ?? null
    if (nextValue !== currentValue) {
      fields[key] = nextValue
    }
  }
  if (!Object.keys(fields).length) {
    ElMessage.warning('没有可提交的修改')
    return
  }
  modifying.value = true
  try {
    await api.post(`/projects/${props.pid}/rules/${detail.value.id}/confirm`, {
      action: 'modify',
      user: 'web-user',
      note: note.value,
      fields
    })
    Object.assign(detail.value, fields, { state: 'modified' })
    modifyOpen.value = false
    ElMessage.success('规则已修改')
    await load()
  } finally {
    modifying.value = false
  }
}

async function batchConfirm() {
  if (!selected.value.length) return
  await api.post(`/projects/${props.pid}/rules/confirm-batch`, {
    ids: selected.value, action: 'confirm', user: 'web-user'
  })
  ElMessage.success(`已批量确认 ${selected.value.length} 条规则`)
  selected.value = []
  load()
}

async function exportExcel() {
  const r = await api.get(`/projects/${props.pid}/rules/export`, {
    responseType: 'blob'
  })
  const url = URL.createObjectURL(r.data)
  const a = document.createElement('a')
  a.href = url
  a.download = '业务规则清单.xlsx'
  a.click()
  URL.revokeObjectURL(url)
}

watch(() => props.pid, load)
onMounted(load)
</script>

<style scoped>
.filters { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.count { margin-left: auto; color: #6b7280; }
.mt16 { margin-top: 16px; }
.pre { white-space: pre-wrap; margin: 0; font-size: 12px; line-height: 1.6; }
.code { background: #111827; color: #d1d5db; padding: 12px; border-radius: 6px;
        max-height: 300px; overflow: auto; }
.snippet-title { font-weight: 600; margin: 14px 0 6px; }
.actions { margin-top: 16px; display: flex; gap: 10px; align-items: center; }
:deep(.row-discarded) { opacity: 0.5; }
</style>
