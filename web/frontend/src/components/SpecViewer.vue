<template>
  <div>
    <div class="sec-title">模块职责</div>
    <p class="purpose">{{ analysis.purpose }}</p>

    <div class="sec-title">功能点（{{ features.length }}）</div>
    <el-collapse>
      <el-collapse-item v-for="(f, i) in features" :key="i" :name="i">
        <template #title>{{ i + 1 }}. {{ f.name }}</template>
        <div class="desc">{{ f.description }}</div>
        <div v-if="f.key_rules?.length" class="krs">
          关键规则：
          <el-tag v-for="(k, j) in f.key_rules.slice(0, 6)" :key="j"
                  size="small" class="mr4 mt4">{{ k }}</el-tag>
        </div>
        <div v-if="f.evidence" class="evi">证据：{{ f.evidence }}</div>
      </el-collapse-item>
    </el-collapse>

    <div class="sec-title">业务流程（{{ processes.length }}）</div>
    <div v-for="(p, i) in processes" :key="i" class="proc">
      <div class="proc-name">{{ p.name }}</div>
      <el-steps direction="vertical" :active="(p.steps || []).length"
                class="mt8" finish-status="success">
        <el-step v-for="(st, j) in p.steps" :key="j" :title="st" />
      </el-steps>
    </div>

    <div class="sec-title">数据模型（{{ dataModel.length }}）</div>
    <el-collapse>
      <el-collapse-item v-for="(dm, i) in dataModel" :key="i" :name="i">
        <template #title>{{ dm.object }}</template>
        <div>表：
          <el-tag v-for="t in dm.tables" :key="t" size="small" type="success"
                  class="mr4 mt4">{{ t }}</el-tag>
        </div>
        <div v-if="dm.key_fields?.length" class="fields mt8">
          关键字段：{{ dm.key_fields.slice(0, 15).join('、') }}
        </div>
        <div v-for="(r, j) in dm.relations" :key="j" class="rel mt4">关系：{{ r }}</div>
      </el-collapse-item>
    </el-collapse>

    <div class="sec-title">业务规则（{{ businessRules.length }}）</div>
    <ul class="rules-list">
      <li v-for="(r, i) in businessRules" :key="i">
        <b>{{ r.name }}</b>：当{{ r.condition }}，则{{ r.action }}
        <span v-if="r.source" class="src">（{{ r.source }}）</span>
      </li>
    </ul>

    <div class="sec-title">外部依赖</div>
    <el-tag v-for="(d, i) in externalDeps" :key="i" size="small"
            class="mr4 mt4">{{ d }}</el-tag>

    <div class="sec-title danger">信息缺口（新系统重建需补全）</div>
    <el-alert v-for="(g, i) in gaps" :key="i" class="mb8"
              :title="g.item" :description="'建议：' + (g.suggestion || '')"
              type="warning" :closable="false" />
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  analysis: { type: Object, default: () => ({}) }
})

const features = computed(() => props.analysis?.features || [])
const processes = computed(() => props.analysis?.processes || [])
const dataModel = computed(() => props.analysis?.data_model || [])
const businessRules = computed(() => props.analysis?.business_rules || [])
const externalDeps = computed(() => props.analysis?.external_dependencies || [])
const gaps = computed(() => props.analysis?.gaps || [])
</script>

<style scoped>
.sec-title { font-weight: 700; font-size: 14px; margin: 18px 0 8px; }
.sec-title.danger { color: #b91c1c; }
.purpose { color: #374151; line-height: 1.8; margin: 0; }
.desc { color: #4b5563; line-height: 1.7; }
.krs { margin-top: 6px; }
.evi { color: #0369a1; font-size: 12px; margin-top: 4px; }
.proc { margin-bottom: 14px; }
.proc-name { font-weight: 600; }
.fields { color: #6b7280; font-size: 12px; }
.rel { color: #6b7280; font-size: 12px; }
.rules-list { padding-left: 18px; color: #374151; line-height: 1.9; font-size: 13px; }
.src { color: #9ca3af; font-size: 12px; }
.mr4 { margin-right: 4px; }
.mt4 { margin-top: 4px; }
.mt8 { margin-top: 8px; }
.mb8 { margin-bottom: 8px; }
</style>
