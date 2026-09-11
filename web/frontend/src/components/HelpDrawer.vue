<template>
  <el-drawer v-model="visible" title="帮助与名词解释" size="460px" append-to-body>
    <div class="help-body">
      <h3>第一次使用？三步出结果</h3>
      <ol class="help-steps">
        <li><b>新建项目</b>（右上角）：把代码交给 C2B —— 选本地目录、传 zip 或填 Git 地址。</li>
        <li><b>点击顶部「一键分析」</b>：系统自动完成扫描、AI 业务归纳和模块分析，
          期间可以离开页面，顶部流程条会显示进度。</li>
        <li><b>拿到报告</b>：「文档与验收」页一键下载《客户交付总报告》（Word），
          可直接发给业务方评审；在「流程与规则」页确认规则后可出正式版。</li>
      </ol>
      <el-alert v-if="!llm.has_key && llm.mode !== 'mock'" type="warning" :closable="false" class="mb12"
                title="还没有配置 AI 服务" description="点右上角「LLM 设置」填入 API Key（如 DeepSeek）。
                不配置也能完成扫描和看图谱，但无法自动归纳业务规则。" />

      <h3>名词解释（都是什么意思）</h3>
      <el-collapse class="help-glossary">
        <el-collapse-item v-for="g in glossary" :key="g.term" :title="g.term">
          <div class="glossary-desc">{{ g.desc }}</div>
        </el-collapse-item>
      </el-collapse>

      <h3>页面上会看到的标签</h3>
      <ul class="help-list">
        <li><b>已确认 / AI 推断（未确认）</b>：这条结论是人工核实过的，还是 AI 猜的。AI 的结论要确认后才算数。</li>
        <li><b>草稿 / 正式</b>：草稿包含 AI 未确认内容（适合内部评审）；正式版只含人工确认过的知识（适合交付客户）。</li>
        <li><b>MOCK / API 未配Key</b>（右上角）：MOCK 表示当前用模拟数据演示流程，不是真实分析。</li>
      </ul>

      <h3>常见问题</h3>
      <el-collapse class="help-glossary">
        <el-collapse-item title="点了一键分析没反应 / 提示失败？">
          <div class="glossary-desc">看提示内容：没配 AI Key 时只做扫描；Key 不对或没余额会在提示中说明。
            项目已有任务在运行时需要等它完成（流程条会转）。</div>
        </el-collapse-item>
        <el-collapse-item title="生成的文档里内容很少？">
          <div class="glossary-desc">正式口径只包含人工确认过的知识。请先到「流程与规则」页确认规则
            （可用「按可信度一键确认」），或先出草稿版用于评审。</div>
        </el-collapse-item>
        <el-collapse-item title="数据会传到别处吗？">
          <div class="glossary-desc">代码和图谱数据都保存在本机（Desktop 版仅保存在这台 Mac）。
            只有「AI 标注」会把少量代码证据片段发送给你自己配置的大模型服务。</div>
        </el-collapse-item>
      </el-collapse>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref } from 'vue'

const visible = defineModel({ type: Boolean, default: false })

defineProps({
  llm: { type: Object, default: () => ({ has_key: false, mode: 'api' }) }
})

const glossary = [
  { term: '模块', desc: '把代码按目录划分出的“功能区”，相当于系统的零件划分。模块越大说明这块代码越多。' },
  { term: '调用边', desc: '“A 函数调用了 B 函数”这样的一条关系。调用边越多，说明功能之间的联系越紧密。' },
  { term: '业务域 / 业务对象', desc: '业务域是业务的“大类”（如订单、库存）；业务对象是大类里具体管理的东西（如订单单据）。' },
  { term: '规则卡片', desc: '一条业务规则的“档案”：什么条件下、系统做什么、数据怎么变、依据在代码哪个位置。' },
  { term: 'AI 可信度（置信度）', desc: 'AI 对这条结论有多大把握（0-100%）。只是参考分，最终以人工确认为准。' },
  { term: 'V1-V4（静态证据）', desc: '结论在代码里的证据强度：V4 表示多处代码相互印证，V1 表示只有单一线索，V0 只是推测。' },
  { term: 'R0-R3（运行验证）', desc: '结论有没有被“实际运行”证实：R0 没验证过，R1 测试验证过，R2 预发布，R3 生产环境验证过。' },
  { term: '模块需求规格', desc: '按模块整理的“重建说明书”：这个模块负责什么、有哪些功能和规则，给未来重构/新系统开发团队用。' },
  { term: '程序入口', desc: '系统从哪里被“启动”：定时任务、对外接口、消息处理、界面操作等。' },
]
</script>

<style scoped>
.help-body { font-size: 13px; line-height: 1.8; color: #374151; }
.help-body h3 { font-size: 14px; margin: 18px 0 8px; }
.help-steps { padding-left: 18px; margin: 0; }
.help-list { padding-left: 18px; margin: 0; }
.glossary-desc { color: #4b5563; }
.mb12 { margin-bottom: 12px; }
</style>
