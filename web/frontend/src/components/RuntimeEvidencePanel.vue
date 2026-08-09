<template>
  <section class="runtime-panel">
    <div class="runtime-head">
      <div>
        <strong>运行验证</strong>
        <el-tag size="small" class="level" :type="levelType(level)">{{ level }}</el-tag>
      </div>
      <el-button size="small" type="primary" plain @click="dialogOpen = true">
        添加运行证据
      </el-button>
    </div>
    <div class="axis-note">
      R 等级只反映实际测试或环境观测，不会修改 AI 可信度、静态证据等级或人工确认状态。
    </div>
    <el-empty v-if="!items.length" description="R0：尚未录入运行证据" :image-size="50" />
    <el-timeline v-else class="evidence-list">
      <el-timeline-item v-for="item in items" :key="item.id"
                        :timestamp="formatDate(item.observed_at || item.created_at)">
        <div class="item-head">
          <span>
            <el-tag size="small" :type="levelType(item.level)">{{ item.level }}</el-tag>
            {{ environmentLabel(item.environment) }} · {{ typeLabel(item.evidence_type) }}
          </span>
          <el-button text type="danger" size="small" @click="remove(item)">删除</el-button>
        </div>
        <div class="summary">{{ item.summary }}</div>
        <div v-if="item.reference" class="reference">引用：{{ item.reference }}</div>
        <div class="author">记录人：{{ item.created_by || '-' }}</div>
      </el-timeline-item>
    </el-timeline>

    <el-dialog v-model="dialogOpen" title="添加运行证据" width="620px" append-to-body>
      <el-form label-width="92px">
        <el-form-item label="运行等级" required>
          <el-select v-model="form.level" style="width: 100%" @change="syncEnvironment">
            <el-option label="R1 测试执行证据" value="R1" />
            <el-option label="R2 受控/预发布环境证据" value="R2" />
            <el-option label="R3 生产环境证据" value="R3" />
          </el-select>
        </el-form-item>
        <el-form-item label="环境" required>
          <el-select v-model="form.environment" style="width: 100%">
            <el-option v-for="item in environmentOptions" :key="item.value"
                       :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="证据类型" required>
          <el-select v-model="form.evidence_type" style="width: 100%">
            <el-option label="测试结果" value="test" />
            <el-option label="调用追踪" value="trace" />
            <el-option label="运行日志" value="log" />
            <el-option label="数据核对" value="reconciliation" />
            <el-option label="人工观测" value="manual_observation" />
          </el-select>
        </el-form-item>
        <el-form-item label="证据摘要" required>
          <el-input v-model="form.summary" type="textarea" :rows="4"
                    maxlength="4000" show-word-limit
                    placeholder="说明执行了什么、输入条件、实际结果，以及它验证了哪项结论" />
        </el-form-item>
        <el-form-item label="引用位置">
          <el-input v-model="form.reference" maxlength="2048"
                    placeholder="测试名、日志查询条件、Trace ID、报告路径或数据核对单号" />
        </el-form-item>
        <el-form-item label="观测时间">
          <el-date-picker v-model="form.observed_at" type="datetime"
                          value-format="YYYY-MM-DDTHH:mm:ss"
                          placeholder="不填则只记录创建时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="记录人">
          <el-input v-model="form.created_by" maxlength="100" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存证据</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const props = defineProps({
  pid: { type: [Number, String], required: true },
  targetType: { type: String, required: true },
  targetId: { type: [Number, String], required: true },
  initialLevel: { type: String, default: 'R0' },
  initialItems: { type: Array, default: null }
})
const emit = defineEmits(['changed'])
const items = ref([])
const level = ref('R0')
const dialogOpen = ref(false)
const saving = ref(false)
const form = reactive({
  level: 'R1', environment: 'test', evidence_type: 'test', summary: '',
  reference: '', observed_at: null, created_by: 'local-user'
})

const environments = {
  R1: [{ value: 'test', label: '测试环境' }],
  R2: [
    { value: 'controlled', label: '受控环境' },
    { value: 'staging', label: '预发布环境' }
  ],
  R3: [{ value: 'production', label: '生产环境' }]
}
const environmentOptions = computed(() => environments[form.level] || [])
const levelType = value => ({ R3: 'success', R2: 'warning', R1: 'info', R0: 'info' }[value] || 'info')
const environmentLabel = value => ({
  test: '测试环境', controlled: '受控环境', staging: '预发布环境', production: '生产环境'
}[value] || value)
const typeLabel = value => ({
  test: '测试结果', trace: '调用追踪', log: '运行日志',
  reconciliation: '数据核对', manual_observation: '人工观测'
}[value] || value)
const formatDate = value => value ? String(value).replace('T', ' ').slice(0, 19) : '-'

function syncEnvironment() {
  form.environment = environmentOptions.value[0]?.value || ''
  if (form.level === 'R1') form.evidence_type = 'test'
}

async function load() {
  if (!props.pid || !props.targetId) return
  const response = (await api.get(`/projects/${props.pid}/runtime-evidence`, {
    params: { target_type: props.targetType, target_id: props.targetId }
  })).data
  items.value = response.items || []
  level.value = response.runtime_level || 'R0'
}

async function save() {
  if (!form.summary.trim()) {
    ElMessage.warning('请填写证据摘要')
    return
  }
  saving.value = true
  try {
    const response = (await api.post(`/projects/${props.pid}/runtime-evidence`, {
      target_type: props.targetType,
      target_id: Number(props.targetId),
      ...form,
      summary: form.summary.trim(),
      reference: form.reference.trim() || null
    })).data
    level.value = response.runtime_level
    dialogOpen.value = false
    form.summary = ''
    form.reference = ''
    form.observed_at = null
    await load()
    emit('changed', level.value)
    ElMessage.success('运行证据已保存')
  } finally {
    saving.value = false
  }
}

async function remove(item) {
  try {
    await ElMessageBox.confirm('确定删除这条运行证据吗？', '删除运行证据', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消'
    })
  } catch {
    return
  }
  const response = (await api.delete(
    `/projects/${props.pid}/runtime-evidence/${item.id}`
  )).data
  level.value = response.runtime_level
  await load()
  emit('changed', level.value)
  ElMessage.success('运行证据已删除')
}

watch(
  () => [props.pid, props.targetType, props.targetId, props.initialItems],
  () => {
    if (Array.isArray(props.initialItems)) {
      items.value = [...props.initialItems]
      level.value = props.initialLevel || 'R0'
    } else {
      load()
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.runtime-panel { margin-top: 16px; padding-top: 14px; border-top: 1px solid #e5e7eb; }
.runtime-head, .item-head { display: flex; align-items: center; justify-content: space-between; }
.level { margin-left: 8px; }
.axis-note, .author, .reference { color: #6b7280; font-size: 12px; }
.axis-note { margin: 7px 0 12px; line-height: 1.6; }
.evidence-list { margin-top: 14px; }
.summary { margin-top: 5px; color: #374151; line-height: 1.6; }
.reference, .author { margin-top: 4px; overflow-wrap: anywhere; }
</style>
