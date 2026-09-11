<template>
  <div>
    <GuideBanner title="文档与交付物 —— 生成与下载交付文档"
                  :desc="'① 客户交付总报告：把概览、功能、流程、核心规则、验收与待确认合成'
                    + '一份给客户/业务人员看的 Word，推荐从这里开始；② 专项交付文档：'
                    + '现状说明书/功能清单/规则清单等，按需单独生成；③ 模块需求规格'
                    + '（面向重建团队）：打包 zip 下载。正式文档只纳入人工确认的业务知识；'
                    + '需要内部评审时，可切换为含 AI 未确认项的草稿。'"
                  status-text="按需生成" status-type="info"
                  action-text="前往模块需求规格页"
                  action-page="/module-specs" />

    <el-card shadow="never" class="mb16 combined-card">
      <div class="combined-wrap">
        <div class="combined-info">
          <div class="combined-title">客户交付总报告</div>
          <div class="combined-desc">
            一份文档讲清整个系统：系统概览、业务功能、业务流程与主线、核心规则、
            数据与入口概览、验收清单和待确认事项。面向不需要读代码的客户与业务人员。
          </div>
        </div>
        <div class="combined-actions">
          <el-button size="large" type="primary" :loading="genKey === 'combined-word'"
                     @click="generate('combined', 'word')">
            下载 Word 总报告
          </el-button>
          <el-button size="large" :loading="genKey === 'combined'"
                     @click="previewCombined">在网页中预览</el-button>
        </div>
      </div>
    </el-card>

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
      <template #header>专项交付文档（面向客户汇报）</template>
      <div class="flex-between mb12">
        <div class="mod-spec-desc">
          系统现状说明书、功能清单、规则清单等专项文档，按需单独生成。
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
          <el-card shadow="hover" class="doc-card" :class="{ 'doc-card-combined': t.key === 'combined' }">
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
      <div v-if="preview" class="md-preview" v-html="previewHtml"></div>
      <el-empty v-else description="选择上方文档类型生成预览" />
    </el-card>

    <el-card shadow="never" class="mt16" v-if="history.length">
      <template #header>
        <div class="flex-between">
          <span>生成记录（本机保留，可随时重新下载）</span>
          <el-button size="small" text type="danger" @click="clearHistory">清空记录</el-button>
        </div>
      </template>
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
import { marked } from 'marked'
import api from '../api'
import GuideBanner from '../components/GuideBanner.vue'
import { friendlyError } from '../errors'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const types = ref([])
const preview = ref('')
const previewTitle = ref('')
const genKey = ref('')
const genAll = ref(false)
const history = ref([])
const draft = ref(false)

// 预览渲染：先转义全部 HTML，只放行后端表格单元格使用的 <br>，避免注入
function renderMarkdown(md) {
  const BR = '\u0001BR\u0001'
  const escaped = md
    .replaceAll('<br>', BR)
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
  return marked.parse(escaped).replaceAll(BR, '<br/>')
}

const previewHtml = ref('')

function historyKey() {
  return `c2b-doc-history-${props.pid ?? 'none'}`
}

function loadHistory() {
  try {
    history.value = JSON.parse(localStorage.getItem(historyKey()) || '[]')
  } catch {
    history.value = []
  }
}

function saveHistory() {
  try {
    localStorage.setItem(historyKey(), JSON.stringify(history.value.slice(0, 60)))
  } catch {
    // 存储满等场景忽略：记录是便利功能，不阻塞主流程
  }
}

function clearHistory() {
  history.value = []
  localStorage.removeItem(historyKey())
}

function pushHistory(title, fmt, content) {
  history.value.unshift({
    time: new Date().toLocaleString(), title, fmt, content
  })
  saveHistory()
}

async function previewCombined() {
  if (!props.pid) return
  genKey.value = 'combined'
  try {
    const r = await api.get(`/projects/${props.pid}/documents/combined`, {
      params: { fmt: 'md', draft: draft.value }, responseType: 'text'
    })
    preview.value = r.data
    previewTitle.value = '客户交付总报告'
    previewHtml.value = renderMarkdown(r.data)
  } catch (e) {
    ElMessage.error(friendlyError(e, '总报告生成失败'))
  } finally {
    genKey.value = null
  }
}

async function generateAll() {
  if (!props.pid) return
  genAll.value = true
  let ok = 0
  for (const t of types.value) {
    genKey.value = t.key
    try {
      const r = await api.get(`/projects/${props.pid}/documents/${t.key}`, {
        params: { fmt: 'md', draft: draft.value }, responseType: 'text'
      })
      pushHistory(t.title, 'md', r.data)
      ok += 1
    } catch (e) {
      ElMessage.error(friendlyError(e, `${t.title} 生成失败`))
    }
  }
  genKey.value = null
  genAll.value = false
  if (ok) ElMessage.success(`${ok} 份文档已生成（见下方记录，可重新下载）`)
}

function reDownload(row) {
  download(row.content, row.title, 'md')
}

async function downloadSpecsZip() {
  if (!props.pid) return
  try {
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
  } catch (e) {
    ElMessage.error(friendlyError(e, '模块需求规格打包失败'))
  }
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
      pushHistory(t.title, 'word', null)
      ElMessage.success('Word 文档已下载')
    } else {
      preview.value = r.data
      previewTitle.value = t.title
      previewHtml.value = renderMarkdown(r.data)
      pushHistory(t.title, 'md', r.data)
    }
  } catch (e) {
    ElMessage.error(friendlyError(e, '文档生成失败'))
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
  previewHtml.value = ''
  loadHistory()
})
watch(draft, () => {
  // 口径切换后旧预览不再代表当前选择，清空避免误读
  preview.value = ''
  previewTitle.value = ''
  previewHtml.value = ''
})
onMounted(() => {
  loadTypes()
  loadHistory()
})
</script>

<style scoped>
.combined-card :deep(.el-card__body) { padding: 20px 24px; }
.combined-wrap { display: flex; justify-content: space-between; align-items: center; gap: 24px; }
.combined-title { font-size: 18px; font-weight: 800; color: #1d4ed8; }
.combined-desc { color: #4b5563; font-size: 13px; line-height: 1.7; margin-top: 6px; max-width: 640px; }
.combined-actions { display: flex; gap: 12px; flex-shrink: 0; }
.doc-card { margin-bottom: 16px; }
.doc-card-combined { border-color: #1d4ed8; }
.doc-title { font-size: 15px; font-weight: 700; }
.doc-desc { color: #6b7280; font-size: 12px; margin: 8px 0 12px; min-height: 34px; }
.doc-actions { display: flex; gap: 8px; }
.doc-mode-actions { display: flex; align-items: center; gap: 14px; flex-shrink: 0; }
.mt16 { margin-top: 16px; }
.mb16 { margin-bottom: 16px; }
.mb12 { margin-bottom: 12px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.mod-spec-desc { color: #4b5563; font-size: 13px; line-height: 1.7; }
.md-preview { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
             padding: 20px 24px; max-height: 640px; overflow: auto;
             font-size: 13px; line-height: 1.8; color: #111827; }
.md-preview :deep(h1) { font-size: 20px; margin: 12px 0; }
.md-preview :deep(h2) { font-size: 16px; margin: 16px 0 8px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }
.md-preview :deep(h3) { font-size: 14px; margin: 12px 0 6px; }
.md-preview :deep(table) { border-collapse: collapse; width: 100%; margin: 8px 0 14px; font-size: 12px; }
.md-preview :deep(th), .md-preview :deep(td) { border: 1px solid #d1d5db; padding: 5px 8px; text-align: left; }
.md-preview :deep(th) { background: #f3f4f6; }
.md-preview :deep(blockquote) { color: #6b7280; border-left: 3px solid #d1d5db; margin: 8px 0; padding-left: 10px; }
</style>
