import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/admin-users'

export interface AdminUserEntry {
  id: number | null
  platform: string
  user_id: string
  created_by: string
  note: string
}

export interface AdminUsersResponse {
  success: boolean
  items: AdminUserEntry[]
  total: number
}

export interface AddAdminUserBody {
  user_id: string
  platform?: string
  note?: string
}

export function listAdminUsers(): Promise<AdminUsersResponse> {
  return backendApi.get<AdminUsersResponse>(API_BASE)
}

export function addAdminUser(body: AddAdminUserBody): Promise<{ success: boolean; item: AdminUserEntry }> {
  return backendApi.post<{ success: boolean; item: AdminUserEntry }>(API_BASE, { body })
}

export function deleteAdminUser(userId: string, platform: string): Promise<{ success: boolean }> {
  const query = platform ? `?platform=${encodeURIComponent(platform)}` : ''
  return backendApi.delete<{ success: boolean }>(`${API_BASE}/${encodeURIComponent(userId)}${query}`)
}

// ---- 外置引擎（本机 CLI / 网络 HTTP 统一视图）----

const ENGINES_BASE = '/api/webui/external-engines'

export interface ExternalEngineItem {
  name: string
  kind: 'http' | 'cli'
  base_url?: string
  model?: string
  command?: string[]
  timeout_seconds: number
}

export interface ExternalEnginesResponse {
  success: boolean
  enable: boolean
  items: ExternalEngineItem[]
  total: number
}

export interface EngineTestResult {
  success: boolean
  engine: string
  elapsed_ms: number
  output_chars: number
  preview: string
}

export function listExternalEngines(): Promise<ExternalEnginesResponse> {
  return backendApi.get<ExternalEnginesResponse>(ENGINES_BASE)
}

export function testExternalEngine(name: string, question: string): Promise<EngineTestResult> {
  return backendApi.post<EngineTestResult>(`${ENGINES_BASE}/${encodeURIComponent(name)}/test`, {
    body: { question },
  })
}
