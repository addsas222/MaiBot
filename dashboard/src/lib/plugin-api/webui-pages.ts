import { getApiBaseUrl } from '@/lib/api-base'
import { backendApi } from '@/lib/http'

export interface PluginWebuiPage {
  plugin_id: string
  name: string
  description: string
  /** 插件 WebUI 入口地址，已按运行环境补全后端 base URL */
  entry: string
}

export async function getPluginWebuiPages(): Promise<PluginWebuiPage[]> {
  const response = await backendApi.get<{ success: boolean; plugins: PluginWebuiPage[] }>(
    '/api/webui/plugins/ui',
    { errorMessage: '加载插件页面列表失败' }
  )
  const baseUrl = await getApiBaseUrl()
  return (response.plugins ?? []).map((plugin) => ({
    ...plugin,
    entry: `${baseUrl}${plugin.entry}`,
  }))
}
