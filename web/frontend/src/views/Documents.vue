<template>
  <div>
    <GuideBanner title="交付物中心 —— 导出交付文档"
                  :desc="'两类交付物：① 交付文档（面向客户汇报：现状说明书/功能清单/规则清单等）'
                    + '—— 选择下方模板生成并下载；② 模块需求规格（面向新系统重建团队）'
                    + '—— 130 份逐模块规格，打包 zip 下载。正式文档只纳入人工确认的业务知识；'
                    + '需要内部评审时，可切换为含 AI 未确认项的草稿。'"
                  status-text="按需生成" status-type="info"
                  action-text="前往模块需求规格页"
                  action-page="/module-specs" />
    <el-card shadow="never" class="mb16">
      <template #header>模块需求规格（重建团队用）</template>
      <div class="flex-between">
        <div class="mod-spec-desc">
          全部模块的需求规格打包下载（zip：每模块一份 Markdown + 索引）。
          下载后即为新系统重建的需求基线。
        </div>
        <el-button type="primary" @click="downloadSpecsZip">打包下载模块需求规格</el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="mb16">
      <template #header>交付文档（面向客户汇报）</template>
      <div class="flex-between mb12">
        <div class="mod-spec-desc">
          系统现状说明书、功能清单、规则清单等 6 类文档，选择模板生成后预览/下载。
          <b>建议：先在「流程与规则」页完成人工确认，再生成正式交付文档。</b>
        </div>
        <div class="doc-mode-actions">
          <el-switch v-model="draft" active-text="草稿（含 AI 未确认项）"
                     inactive-text="正式（仅人工确认项）" />
          <el-button type="primary" :loading="genAll" @click="generateAll">
            一键生成全部文档
          </el-button>
        </div>
      </div>
      <el-row :gutter="16">
        <el-col v-for="t in types" :key="t.key" :span="8">
          <el-card shadow="hover" class="doc-card">
            <div class="doc-title">{{ t.title }}</div>
            <div class="doc-desc">{{ t.desc }}</div>
            <div class="doc-actions">
              <el-button size="small" type="primary" :loading="genKey === t.key"
                         @click="generate(t.key, 'md')">生成 Markdown</el-button>
              <el-button size="small" :loading="genKey === t.key"
                         @click="generate(t.key, 'word')">生成 Word</el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </el-card>

    <el-card shadow="never" class="mt16">
      <template #header>
        <div class="flex-between">
          <span>文档预览（{{ previewTitle }}）</span>
          <el-button v-if="preview" size="small"
                     @click="download(preview, previewTitle, 'md')">下载 Markdown</el-button>
        </div>
      </template>
      <pre class="preview" v-if="preview">{{ preview }}</pre>
      <el-empty v-else description="选择上方文档类型生成预览" />
    </el-card>

    <el-card shadow="never" class="mt16" v-if="history.length">
      <template #header>本次会话生成记录</template>
      <el-table :data="history" size="small">
        <el-table-column prop="time" label="时间" width="180" />
        <el-table-column prop="title" label="文档" min-width="200" />
        <el-table-column prop="fmt" label="格式" width="80" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" text @click="reDownload(row)">重新下载</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import GuideBanner from '../components/GuideBanner.vue'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const types = ref([])
const preview = ref('')
const previewTitle = ref('')
const genKey = ref('')
const genAll = ref(false)
const history = ref([])
const draft = ref(false)

async function generateAll() {
  if (!props.pid) return
  genAll.value = true
  for (const t of types.value) {
    genKey.value = t.key
    try {
      const r = await api.get(`/projects/${props.pid}/documents/${t.key}`, {
        params: { fmt: 'md', draft: draft.value }, responseType: 'text'
      })
      history.value.unshift({
        time: new Date().toLocaleString(), title: t.title, fmt: 'md', content: r.data
      })
    } catch (e) {
      ElMessage.error(`${t.title} 生成失败：${e?.response?.data?.detail || e}`)
    }
  }
  genKey.value = null
  genAll.value = false
  ElMessage.success('全部文档已生成（见下方记录，可直接下载）')
}

function reDownload(row) {
  download(row.content, row.title, 'md')
}

async function downloadSpecsZip() {
  if (!props.pid) return
  const r = await api.get(`/projects/${props.pid}/modules/analyses/export`, {
    responseType: 'blob'
  })
  const url = URL.createObjectURL(r.data)
  const a = document.createElement('a')
  a.href = url
  a.download = 'module-specs.zip'
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('模块需求规格已打包下载')
}

async function loadTypes() {
  types.value = (await api.get('/documents/types')).data
}

async function generate(key, fmt) {
  if (!props.pid) return
  genKey.value = key
  try {
    const r = await api.get(`/projects/${props.pid}/documents/${key}`, {
      params: { fmt, draft: draft.value }, responseType: fmt === 'word' ? 'blob' : 'text'
    })
    const t = types.value.find(x => x.key === key)
    if (fmt === 'word') {
      download(r.data, t.title, 'docx')
      ElMessage.success('Word 文档已下载')
    } else {
      preview.value = r.data
      previewTitle.value = t.title
    }
  } finally {
    genKey.value = null
  }
}

function download(data, name, ext) {
  const blob = data instanceof Blob ? data
    : new Blob([data], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${name}.${ext}`
  a.click()
  URL.revokeObjectURL(url)
}

watch(() => props.pid, () => {
  preview.value = ''
  previewTitle.value = ''
  history.value = []
})
onMounted(loadTypes)
</script>

<style scoped>
.doc-card { margin-bottom: 16px; }
.doc-title { font-size: 15px; font-weight: 700; }
.doc-desc { color: #6b7280; font-size: 12px; margin: 8px 0 12px; min-height: 34px; }
.doc-actions { display: flex; gap: 8px; }
.doc-mode-actions { display: flex; align-items: center; gap: 14px; flex-shrink: 0; }
.mt16 { margin-top: 16px; }
.mb16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.mod-spec-desc { color: #4b5563; font-size: 13px; line-height: 1.7; }
.preview { background: #111827; color: #d1d5db; padding: 16px; border-radius: 6px;
           font-size: 12px; line-height: 1.7; max-height: 560px; overflow: auto;
           white-space: pre-wrap; }
</style>
