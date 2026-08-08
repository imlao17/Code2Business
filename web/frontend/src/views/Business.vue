<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>业务对象（{{ objects.length }}）</template>
          <el-empty v-if="!objects.length" description="暂无业务对象（运行标注后生成）" />
          <el-collapse v-else>
            <el-collapse-item v-for="o in objects" :key="o.id" :name="o.id">
              <template #title>
                <span class="obj-name">{{ o.name }}</span>
                <el-tag size="small" class="ml8" type="info">{{ o.confidence }}%</el-tag>
              </template>
              <div class="obj-desc">{{ o.description }}</div>
              <div class="section">业务动作</div>
              <el-tag v-for="a in o.actions" :key="a" size="small" class="mr4">{{ a }}</el-tag>
              <div class="section">来源数据表（{{ (o.source_tables || []).length }}）</div>
              <div class="chip-wrap">
                <el-tag v-for="t in (o.source_tables || []).slice(0, 20)" :key="t"
                        size="small" type="success" class="mr4 mt4">{{ t }}</el-tag>
              </div>
              <div class="section">来源类</div>
              <div class="chip-wrap">
                <el-tag v-for="c in (o.source_classes || []).slice(0, 15)" :key="c"
                        size="small" class="mr4 mt4">{{ c }}</el-tag>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>业务流程（{{ flows.length }}）</template>
          <el-empty v-if="!flows.length" description="暂无流程（运行标注后生成）" />
          <el-collapse v-else>
            <el-collapse-item v-for="f in flows" :key="f.id" :name="f.id">
              <template #title>
                <span class="obj-name">{{ f.name }}</span>
                <el-tag size="small" class="ml8" type="info">{{ f.confidence }}%</el-tag>
              </template>
              <div class="obj-desc">{{ f.description }}</div>
              <div class="section">业务步骤</div>
              <el-timeline class="mt8">
                <el-timeline-item v-for="(st, i) in f.business_steps" :key="i">
                  {{ st }}
                </el-timeline-item>
              </el-timeline>
              <div class="section">技术调用链（入口：{{ f.entry_name }}）</div>
              <div class="chip-wrap">
                <el-tag v-for="t in (f.tech_steps || []).slice(0, 15)" :key="t"
                        size="small" type="warning" class="mr4 mt4">{{ t }}</el-tag>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import api from '../api'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const objects = ref([])
const flows = ref([])
let loadToken = 0

async function load() {
  const projectId = props.pid
  const token = ++loadToken
  if (!projectId) {
    objects.value = []
    flows.value = []
    return
  }
  const [nextObjects, nextFlows] = await Promise.all([
    api.get(`/projects/${projectId}/biz-objects`),
    api.get(`/projects/${projectId}/flows`)
  ])
  if (token !== loadToken || projectId !== props.pid) return
  objects.value = nextObjects.data
  flows.value = nextFlows.data
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
  window.removeEventListener('project-changed', handleProjectChanged)
})
</script>

<style scoped>
.obj-name { font-weight: 600; }
.obj-desc { color: #4b5563; font-size: 13px; margin-bottom: 8px; }
.section { font-weight: 600; font-size: 12px; color: #6b7280; margin: 10px 0 4px; }
.chip-wrap { display: flex; flex-wrap: wrap; }
.ml8 { margin-left: 8px; }
.mr4 { margin-right: 4px; }
.mt4 { margin-top: 4px; }
.mt8 { margin-top: 8px; }
</style>
