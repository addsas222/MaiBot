import { backendApi } from '@/lib/http'

export interface ExternalApp {
  app_id: string
  name: string
  docs_url: string
  status: 'stopped' | 'running' | 'installing' | 'starting' | 'external' | 'unknown'
  pid?: number | null
  installed: boolean
  port: number
  external_url: string
  install_steps: string[][]
  start_cmd: string[]
  embed_url?: string
  error?: string
  engine_active?: boolean
}

export async function getExternalApps(): Promise<{ apps: ExternalApp[]; active_engine: string | null }> {
  const response = await backendApi.get<{ success: boolean; apps: ExternalApp[]; active_engine: string | null }>(
    '/api/webui/external-apps',
    { errorMessage: '加载外部应用列表失败' }
  )
  return { apps: response.apps ?? [], active_engine: response.active_engine ?? null }
}

export async function activateSubEngine(appId: string): Promise<void> {
  await backendApi.post(`/api/webui/external-apps/${appId}/activate`, {})
}

export async function deactivateSubEngine(): Promise<void> {
  await backendApi.post('/api/webui/external-apps/deactivate', {})
}

export async function getExternalApp(appId: string): Promise<ExternalApp> {
  const response = await backendApi.get<{ success: boolean; app: ExternalApp }>(
    `/api/webui/external-apps/${appId}`,
    { errorMessage: '加载外部应用详情失败' }
  )
  return response.app
}

async function postAction(appId: string, action: 'install' | 'start' | 'stop'): Promise<{ success: boolean; message?: string; error?: string }> {
  return await backendApi.post(`/api/webui/external-apps/${appId}/${action}`, {})
}

export const installExternalApp = (appId: string) => postAction(appId, 'install')
export const startExternalApp = (appId: string) => postAction(appId, 'start')
export const stopExternalApp = (appId: string) => postAction(appId, 'stop')

export async function saveExternalAppConfig(
  appId: string,
  payload: { external_url?: string; port?: number }
): Promise<ExternalApp> {
  const response = await backendApi.put<{ success: boolean; app: ExternalApp }>(
    `/api/webui/external-apps/${appId}/config`,
    { body: payload }
  )
  return response.app
}

export interface CharacterCardSummary {
  card_id: string
  name: string
  spec: string
  tags: string[]
}

export async function listCharacterCards(): Promise<CharacterCardSummary[]> {
  const response = await backendApi.get<{ success: boolean; cards: CharacterCardSummary[] }>(
    '/api/webui/st-import/cards'
  )
  return response.cards ?? []
}

export async function uploadCharacterCard(file: File): Promise<{ success: boolean; card_id: string; error?: string }> {
  const form = new FormData()
  form.append('file', file)
  return await backendApi.request('POST', '/api/webui/st-import/cards', { body: form })
}

export async function deleteCharacterCard(cardId: string): Promise<void> {
  await backendApi.delete(`/api/webui/st-import/cards/${cardId}`)
}

export async function applyCardPersonality(cardId: string): Promise<{ success: boolean; message?: string; error?: string }> {
  return await backendApi.request('POST', `/api/webui/st-import/cards/${cardId}/apply-personality`, {})
}

export async function uploadWorldbook(
  file: File,
  bookName: string
): Promise<{ success: boolean; imported: number; skipped: number; failed: number; total_entries: number; errors: string[] }> {
  const form = new FormData()
  form.append('file', file)
  return await backendApi.request('POST', '/api/webui/st-import/worldbooks', {
    body: form,
    query: { book_name: bookName },
  })
}
