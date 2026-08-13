import { invoke, isTauri } from '@tauri-apps/api/core'

// Keep the browser product free of a desktop-only API dependency at runtime.
// Tauri 2 exposes this marker before Vue mounts, including during `tauri dev`.
export const isDesktop = isTauri()
export const desktopApiBase = 'http://127.0.0.1:8123'

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

export async function waitForDesktopEngine(timeoutMs = 20000) {
  if (!isDesktop) return
  const deadline = Date.now() + timeoutMs
  let lastError = null
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${desktopApiBase}/healthz`, { cache: 'no-store' })
      if (response.ok) return
      lastError = new Error(`本地分析引擎返回 ${response.status}`)
    } catch (error) {
      lastError = error
    }
    await delay(250)
  }
  throw lastError || new Error('本地分析引擎未能启动')
}

export async function chooseLocalSourceDirectory() {
  if (!isDesktop) return null
  return invoke('choose_source_directory')
}
