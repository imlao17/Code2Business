// 把后端/网络错误翻译成普通人能懂的中文提示。
// 命中已知模式给出可操作的建议；未命中时透出截断后的原始信息便于排查。
export function friendlyError(e, fallback = '操作失败') {
  const detail = e?.response?.data?.detail ?? e?.response?.data ?? e?.message ?? e
  const text = typeof detail === 'string' ? detail : JSON.stringify(detail)
  const rules = [
    [/缺少 API Key|LLM 设置|api_key|API Key/i,
      '还没有配置 AI 服务：请点击右上角「LLM 设置」，填入 API Key 后重试。'
      + '不配置也可以先完成扫描和图谱查看。'],
    [/401|Unauthorized|invalid.{0,12}key|authentication|余额|insufficient/i,
      'AI 服务拒绝了当前请求：请检查「LLM 设置」中的 API Key 是否正确、账户是否有余额。'],
    [/connect|timeout|ECONNREFUSED|ENOTFOUND|network|Failed to fetch/i,
      '连不上 AI 服务：请检查网络，以及「LLM 设置」里的服务地址是否可用。'],
    [/database is locked|数据库忙/i,
      '本地数据库正忙（通常是上一个大任务还在写入）。请稍等片刻再试；若反复出现，请重启 C2B。'],
    [/已有运行中的任务|409/,
      '当前项目已有任务在运行，请等它完成后再操作（顶部流程条可查看进度）。'],
    [/不存在|404/,
      '找不到对应的数据或项目，请刷新页面后重试。'],
  ]
  for (const [re, msg] of rules) {
    if (re.test(text)) return msg
  }
  return `${fallback}：${String(text).slice(0, 200)}`
}
