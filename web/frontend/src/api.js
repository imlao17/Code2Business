import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 60000 })

// 单次 LLM 分析的后端超时为 5 分钟；这类请求不应被通用 60 秒前端超时提前截断。
export const LONG_REQUEST_TIMEOUT = 330000

export default api
