<template>
  <el-card shadow="never">
    <template #header>待确认项（{{ questions.length }}）</template>
    <el-empty v-if="!questions.length" description="暂无待确认项" />
    <el-table v-else :data="questions" size="small">
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="qtype" label="类型" width="120">
        <template #default="{ row }">
          <el-tag size="small">{{ row.qtype }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="desc" label="描述" min-width="300" show-overflow-tooltip />
      <el-table-column prop="suggestion" label="建议" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'open' ? 'warning' : 'success'">
            {{ row.status === 'open' ? '待处理' : '已解决' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110">
        <template #default="{ row }">
          <el-button v-if="row.status === 'open'" size="small" type="success" text
                     @click="resolve(row)">标记已解决</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const questions = ref([])

async function load() {
  if (!props.pid) return
  questions.value = (await api.get(`/projects/${props.pid}/questions`)).data
}

async function resolve(row) {
  await api.post(`/projects/${props.pid}/questions/${row.id}/resolve`)
  ElMessage.success('已标记为解决')
  load()
}
watch(() => props.pid, load)
onMounted(load)
</script>
