<template>
  <el-row :gutter="16">
    <el-col :span="12">
      <el-card shadow="never">
        <template #header>数据表（{{ tables.length }}）</template>
        <el-table :data="tables" size="small" height="520" @row-click="loadOps">
          <el-table-column prop="name" label="表名" min-width="160" />
          <el-table-column prop="source" label="来源" width="80" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card shadow="never">
        <template #header>SQL 操作（{{ ops.length }}）</template>
        <el-table :data="ops" size="small" height="520">
          <el-table-column label="操作" width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="opType(row.op)">{{ row.op }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="table" label="表" width="140" />
          <el-table-column label="字段" min-width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ (row.fields || []).slice(0, 8).join(', ') }}</template>
          </el-table-column>
          <el-table-column prop="file" label="位置" min-width="200" show-overflow-tooltip />
          <el-table-column label="可信度" width="80">
            <template #default="{ row }">{{ row.confidence }}%</template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import api from '../api'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const tables = ref([])
const ops = ref([])
const opType = op => ({ insert: 'success', update: 'warning', delete: 'danger', select: 'info' }[op] || 'info')
let tableLoadToken = 0
let opsLoadToken = 0

async function load() {
  const projectId = props.pid
  const token = ++tableLoadToken
  if (!projectId) {
    tables.value = []
    ops.value = []
    return
  }
  ops.value = []
  const data = (await api.get(`/projects/${projectId}/tables`)).data
  if (token !== tableLoadToken || projectId !== props.pid) return
  tables.value = data
}
async function loadOps(row) {
  const projectId = props.pid
  const token = ++opsLoadToken
  if (!projectId) {
    ops.value = []
    return
  }
  const data = (await api.get(`/projects/${projectId}/sql-ops`, {
    params: { table: row.name, limit: 200 }
  })).data
  if (token !== opsLoadToken || projectId !== props.pid) return
  ops.value = data
}
watch(() => props.pid, () => {
  opsLoadToken += 1
  ops.value = []
  load()
})
onMounted(load)
</script>
