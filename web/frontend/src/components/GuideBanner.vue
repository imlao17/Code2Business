<template>
  <el-card shadow="never" class="banner" :class="`tone-${tone}`">
    <div class="content">
      <div class="texts">
        <div class="b-title">{{ title }}</div>
        <div class="b-desc">{{ desc }}</div>
        <div v-if="statusText" class="b-status">
          <el-tag size="small" :type="statusType" class="mr8">{{ statusText }}</el-tag>
          <span v-if="extraText" class="extra">{{ extraText }}</span>
        </div>
      </div>
      <div class="action" v-if="actionText">
        <el-button type="primary" size="small" @click="onAction">
          {{ actionText }}
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  title: { type: String, default: '' },
  desc: { type: String, default: '' },
  statusText: { type: String, default: '' },
  statusType: { type: String, default: 'info' },   // success/warning/info/primary/danger
  extraText: { type: String, default: '' },
  actionText: { type: String, default: '' },
  actionPage: { type: String, default: '' },       // 跳转页面
  tone: { type: String, default: 'default' }        // default/primary/warning
})
const emit = defineEmits(['action'])
const router = useRouter()

const toneClass = computed(() => `tone-${props.tone}`)

function onAction() {
  if (props.actionPage) router.push(props.actionPage)
  else emit('action')
}
</script>

<style scoped>
.banner { margin-bottom: 16px; }
.banner.tone-primary { background: #eff6ff; border-color: #bfdbfe; }
.banner.tone-warning { background: #fffbeb; border-color: #fde68a; }
.banner.tone-default { background: #f9fafb; }
.content { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.texts { flex: 1; }
.b-title { font-weight: 700; font-size: 14px; }
.b-desc { color: #6b7280; font-size: 12px; margin-top: 4px; line-height: 1.6; }
.b-status { margin-top: 8px; display: flex; align-items: center; }
.extra { color: #6b7280; font-size: 12px; }
.action { flex-shrink: 0; }
.mr8 { margin-right: 8px; }
</style>
