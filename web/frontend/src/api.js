import axios from 'axios'
import { desktopApiBase, isDesktop } from './desktop'

// Browser builds keep the relative `/api` proxy.  The packaged macOS app
// talks only to its loopback Python sidecar, never to a remote C2B server.
const api = axios.create({
  baseURL: isDesktop ? `${desktopApiBase}/api` : '/api',
  timeout: 60000
})

// 单次 LLM 分析的后端超时为 5 分钟；这类请求不应被通用 60 秒前端超时提前截断。
export const LONG_REQUEST_TIMEOUT = 330000

export default api
