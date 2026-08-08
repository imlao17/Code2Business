<template>
  <div class="qa-float">
    <el-badge :value="pendingCount" :hidden="!pendingCount" class="badge">
      <el-button class="float-btn" type="primary" circle size="large"
                 @click="open = !open" title="AI 问答">
        <span class="qa-icon">问</span>
      </el-button>
    </el-badge>

    <el-drawer v-model="open" size="440px" :with-header="false"
               :append-to-body="true" class="qa-drawer">
      <div class="qa-body">
        <div class="qa-header">
          <div>
            <div class="qa-title">AI 业务问答</div>
            <div class="qa-sub">基于当前项目的业务知识图谱，回答带证据与可信度</div>
          </div>
          <el-button size="small" text @click="clearInput">清空</el-button>
        </div>

        <div class="qa-chat" ref="chatEl">
          <div v-if="!history.length" class="qa-empty">
            还没有问答记录。试试问：
            <div class="samples">
              <el-tag v-for="s in samples" :key="s" size="small" class="sample"
                      @click="ask(s)">{{ s }}</el-tag>
            </div>
          </div>
          <div v-for="(h, i) in displayHistory" :key="h.id" class="msg">
            <div class="q-row">
              <div class="q-bubble">{{ h.question }}</div>
              <el-button size="small" text type="danger"
                         @click="remove(h.id)">删</el-button>
            </div>
            <div class="a-bubble" v-if="h.error">
              <span class="err">{{ h.error }}</span>
            </div>
            <div class="a-bubble" v-else-if="h.answer">
              <div class="conclusion">{{ h.answer.conclusion }}</div>
              <div class="a-sec" v-if="h.answer.trigger">触发：{{ h.answer.trigger }}</div>
              <div class="a-sec" v-if="h.answer.steps?.length">
                步骤：
                <ol class="steps">
                  <li v-for="(st, j) in h.answer.steps" :key="j">{{ st }}</li>
                </ol>
              </div>
              <div class="a-sec" v-if="h.answer.data_change">数据变化：{{ h.answer.data_change }}</div>
              <div class="a-sec" v-if="h.answer.status_change">状态变化：{{ h.answer.status_change }}</div>
              <div class="a-sec" v-if="h.answer.evidence?.length">
                证据：
                <ul class="evi">
                  <li v-for="(e, j) in h.answer.evidence.slice(0, 4)" :key="j">{{ e }}</li>
                </ul>
              </div>
              <div class="a-meta">
                <el-tag size="small" :type="h.answer.confidence >= 75 ? 'success'
                        : h.answer.confidence >= 50 ? 'warning' : 'danger'">
                  可信度 {{ h.answer.confidence }}%
                </el-tag>
                <span class="a-time">{{ fmtTime(h.created_at) }}</span>
              </div>
              <div v-if="h.answer.uncovered?.length" class="uncovered">
                未覆盖：{{ h.answer.uncovered.slice(0, 3).join('；') }}
              </div>
            </div>
          </div>
        </div>

        <div class="qa-input">
          <el-input v-model="question" placeholder="问：销售订单如何驱动生产计划？"
                    @keyup.enter="send" :disabled="busy" />
          <el-button type="primary" :loading="busy" @click="send">问</el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api, { LONG_REQUEST_TIMEOUT } from '../api'

const props = defineProps({ pid: { type: [Number, String], default: null } })
const open = ref(false)
const busy = ref(false)
const question = ref('')
const history = ref([])
const chatEl = ref(null)
const samples = [
  '销售订单如何驱动生产计划？',
  '采购订单有哪些业务规则？',
  '生产计划触发后会发生什么？',
  '系统中有哪些自动触发的逻辑？'
]
let historyLoadToken = 0

const pendingCount = computed(() => history.value.length)

// 最新在下方（聊天式）：后端按 id 倒序返回，展示时反转
const displayHistory = computed(() => [...history.value].reverse())

async function loadHistory() {
  const projectId = props.pid
  const token = ++historyLoadToken
  if (!projectId) {
    history.value = []
    open.value = false
    busy.value = false
    return
  }
  const data = (await api.get(`/projects/${projectId}/qa-history`)).data
  if (token !== historyLoadToken || projectId !== props.pid) return
  history.value = data
  await nextTick()
  scrollBottom()
}

function scrollBottom() {
  chatEl.value?.scrollTo({ top: chatEl.value.scrollHeight })
}

async function ask(q) {
  const text = (q || question.value).trim()
  if (!text || !props.pid || busy.value) return
  busy.value = true
  try {
    const r = (await api.post(`/projects/${props.pid}/qa`,
      { question: text }, { timeout: LONG_REQUEST_TIMEOUT })).data
    await loadHistory()
    if (!r.ok) ElMessage.error(r.error || '问答失败')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || String(e))
  } finally {
    busy.value = false
    question.value = ''
    await nextTick()
    scrollBottom()
  }
}

function send() {
  ask(question.value)
}

async function remove(id) {
  await api.delete(`/projects/${props.pid}/qa-history/${id}`)
  loadHistory()
}

function clearInput() {
  question.value = ''
}

const fmtTime = t => (t || '').slice(5, 16).replace('T', ' ')

watch(() => props.pid, () => {
  historyLoadToken += 1
  history.value = []
  busy.value = false
  loadHistory()
})
onMounted(loadHistory)
</script>

<style scoped>
.qa-float { position: fixed; right: 24px; bottom: 24px; z-index: 2000; }
.badge { display: block; }
.float-btn { width: 52px; height: 52px; font-size: 18px; box-shadow: 0 4px 12px rgba(0,0,0,.2); }
.qa-icon { font-weight: 700; }
.qa-body { display: flex; flex-direction: column; height: 100%; }
.qa-header { display: flex; justify-content: space-between; align-items: center;
             padding-bottom: 12px; border-bottom: 1px solid #e5e7eb; }
.qa-title { font-weight: 700; font-size: 15px; }
.qa-sub { color: #9ca3af; font-size: 11px; margin-top: 2px; }
.qa-chat { flex: 1; overflow: auto; padding: 12px 2px; }
.qa-empty { color: #9ca3af; font-size: 13px; text-align: center; padding: 40px 0; }
.samples { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; }
.sample { cursor: pointer; }
.msg { margin-bottom: 14px; }
.q-row { display: flex; justify-content: flex-end; align-items: center; gap: 4px; }
.q-bubble { background: #e0f2fe; border-radius: 8px; padding: 6px 10px;
            font-size: 13px; max-width: 85%; }
.a-bubble { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px;
            padding: 8px 10px; font-size: 12px; line-height: 1.7; margin-top: 4px; }
.conclusion { font-weight: 700; margin-bottom: 4px; }
.a-sec { color: #4b5563; margin-top: 4px; }
.steps { margin: 2px 0 0; padding-left: 18px; }
.evi { margin: 2px 0 0; padding-left: 18px; color: #0369a1; }
.a-meta { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.a-time { color: #9ca3af; font-size: 11px; }
.uncovered { color: #b91c1c; font-size: 11px; margin-top: 4px; }
.err { color: #b91c1c; }
.qa-input { display: flex; gap: 8px; padding-top: 10px;
            border-top: 1px solid #e5e7eb; }
</style>
