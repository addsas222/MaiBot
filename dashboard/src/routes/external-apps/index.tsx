import { useRef } from 'react'
import { Link } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { toast } from '@/hooks/use-toast'
import {
  activateSubEngine,
  deactivateSubEngine,
  getExternalApps,
  installExternalApp,
  startExternalApp,
  stopExternalApp,
  type ExternalApp,
} from '@/lib/external-apps-api'
import {
  applyCardPersonality,
  deleteCharacterCard,
  listCharacterCards,
  uploadCharacterCard,
  uploadWorldbook,
} from '@/lib/external-apps-api'

const STATUS_TEXT: Record<ExternalApp['status'], string> = {
  stopped: '未运行',
  running: '运行中',
  installing: '安装中',
  starting: '启动中',
  external: '外挂模式',
  unknown: '未知',
}

function StatusBadge({ status }: { status: ExternalApp['status'] }) {
  const variant =
    status === 'running' ? 'default' : status === 'stopped' ? 'secondary' : 'outline'
  return (
    <Badge variant={variant} className={status === 'installing' || status === 'starting' ? 'animate-pulse' : ''}>
      {STATUS_TEXT[status] ?? status}
    </Badge>
  )
}

function SillyTavernDataSection() {
  const queryClient = useQueryClient()
  const cardInputRef = useRef<HTMLInputElement>(null)
  const bookInputRef = useRef<HTMLInputElement>(null)
  const cardsQuery = useQuery({ queryKey: ['st-character-cards'], queryFn: listCharacterCards })
  const invalidateCards = () => queryClient.invalidateQueries({ queryKey: ['st-character-cards'] })

  const uploadCard = useMutation({
    mutationFn: async (file: File) => {
      const res = await uploadCharacterCard(file)
      if (!res.success) throw new Error(res.error ?? '导入失败')
      return res
    },
    onSuccess: (res) => {
      toast({ title: '角色卡已导入', description: res.card_id })
      invalidateCards()
    },
    onError: (err) => toast({ title: '角色卡导入失败', description: err instanceof Error ? err.message : String(err), variant: 'destructive' }),
  })

  const applyCard = useMutation({
    mutationFn: (cardId: string) => applyCardPersonality(cardId),
    onSuccess: (res) => {
      if (res.success) toast({ title: '已应用为人格设定', description: res.message })
    },
    onError: (err) => toast({ title: '应用失败', description: err instanceof Error ? err.message : String(err), variant: 'destructive' }),
  })

  const removeCard = useMutation({
    mutationFn: (cardId: string) => deleteCharacterCard(cardId),
    onSuccess: () => invalidateCards(),
  })

  const uploadBook = useMutation({
    mutationFn: async (file: File) => {
      const name = file.name.replace(/\.json$/i, '')
      return await uploadWorldbook(file, name)
    },
    onSuccess: (res) => {
      const summary = `导入 ${res.imported} 条 · 跳过 ${res.skipped} · 失败 ${res.failed}（共 ${res.total_entries}）`
      if (res.failed > 0) {
        toast({ title: '世界书部分导入失败', description: `${summary}\n${(res.errors ?? []).slice(0, 3).join('\n')}`, variant: 'destructive' })
      } else {
        toast({ title: '世界书已导入知识库', description: summary })
      }
    },
    onError: (err) => toast({ title: '世界书导入失败', description: err instanceof Error ? err.message : String(err), variant: 'destructive' }),
  })

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>酒馆数据导入</CardTitle>
        <CardDescription>
          导入 SillyTavern 角色卡（PNG/JSON）与世界书：世界书条目将写入长期记忆知识库；角色卡可一键应用为人格设定
        </CardDescription>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            ref={cardInputRef}
            type="file"
            accept=".png,.json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) uploadCard.mutate(file)
            }}
          />
          <Button size="sm" disabled={uploadCard.isPending} onClick={() => cardInputRef.current?.click()}>
            {uploadCard.isPending ? '导入中…' : '导入角色卡'}
          </Button>
          <input
            ref={bookInputRef}
            type="file"
            accept=".json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) uploadBook.mutate(file)
            }}
          />
          <Button size="sm" variant="outline" disabled={uploadBook.isPending} onClick={() => bookInputRef.current?.click()}>
            {uploadBook.isPending ? '导入中…' : '导入世界书'}
          </Button>
        </div>

        {cardsQuery.data && cardsQuery.data.length > 0 && (
          <div className="mt-3 space-y-2">
            {cardsQuery.data.map((card) => (
              <div key={card.card_id} className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
                <span className="min-w-0 truncate font-medium">{card.name}</span>
                <Badge variant="outline">{card.spec}</Badge>
                <div className="ml-auto flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={applyCard.isPending}
                    onClick={() => applyCard.mutate(card.card_id)}
                  >
                    应用为人格
                  </Button>
                  <Button size="sm" variant="ghost" disabled={removeCard.isPending} onClick={() => removeCard.mutate(card.card_id)}>
                    删除
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardHeader>
    </Card>
  )
}

export function ExternalAppsIndexPage() {
  const queryClient = useQueryClient()
  const appsQuery = useQuery({
    queryKey: ['external-apps'],
    queryFn: getExternalApps,
    refetchInterval: (query) =>
      query.state.data?.apps.some((a) => a.status === 'installing' || a.status === 'starting') ? 3000 : false,
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['external-apps'] })

  const action = useMutation({
    mutationFn: async ({ appId, kind }: { appId: string; kind: 'install' | 'start' | 'stop' }) => {
      if (kind === 'install') return await installExternalApp(appId)
      if (kind === 'start') return await startExternalApp(appId)
      return await stopExternalApp(appId)
    },
    onSuccess: (res, vars) => {
      if (!res.success && res.error) {
        toast({ title: `${vars.kind === 'install' ? '安装' : vars.kind === 'start' ? '启动' : '停止'}失败`, description: res.error, variant: 'destructive' })
      }
      setTimeout(invalidate, 800)
    },
    onError: () => setTimeout(invalidate, 800),
  })

  const engineToggle = useMutation({
    mutationFn: async (appId: string | null) => (appId ? await activateSubEngine(appId) : await deactivateSubEngine()),
    onSuccess: () => {
      toast({ title: '子内核状态已更新' })
      invalidate()
    },
  })

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-4 sm:space-y-6 sm:p-6">
        <div className="min-w-0">
          <h1 className="text-xl font-bold sm:text-2xl md:text-3xl">外部应用</h1>
          <p className="text-muted-foreground mt-1 text-xs sm:text-sm">
            一键安装并托管 SillyTavern / Agnai，启动后可直接在麦麦 WebUI 中打开；LLM 源可指向麦麦的 OpenAI 兼容网关
          </p>
        </div>

        {appsQuery.isPending ? (
          <div className="flex h-40 items-center justify-center">
            <ThinkingIllustration size="sm" />
          </div>
        ) : appsQuery.isError ? (
          <p className="text-muted-foreground py-10 text-center text-sm">
            {appsQuery.error instanceof Error ? appsQuery.error.message : '加载外部应用列表失败'}
          </p>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {(appsQuery.data?.apps ?? []).map((app) => (
              <Card key={app.app_id} className="h-full">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <span className="min-w-0 break-all">{app.name}</span>
                    <StatusBadge status={app.status} />
                    {app.engine_active && <Badge>当前子内核</Badge>}
                    <a
                      href={app.docs_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-muted-foreground ml-auto text-xs underline-offset-2 hover:underline"
                    >
                      官方文档
                    </a>
                  </CardTitle>
                  <CardDescription>
                    端口 {app.port}
                    {app.external_url ? ` · 外挂地址 ${app.external_url}` : ''}
                    {' · '}启用为子内核后其人格层（角色卡/世界书）才会驱动麦麦对话，同一时刻仅一个生效
                  </CardDescription>

                  <div className="flex flex-wrap items-center gap-2">
                    {app.engine_active ? (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={engineToggle.isPending}
                        onClick={() => engineToggle.mutate(null)}
                      >
                        停用子内核
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={engineToggle.isPending}
                        onClick={() => engineToggle.mutate(app.app_id)}
                      >
                        启用为子内核
                      </Button>
                    )}
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {!app.installed && app.status !== 'external' && (
                      <Button
                        size="sm"
                        disabled={action.isPending || app.status === 'installing'}
                        onClick={() => action.mutate({ appId: app.app_id, kind: 'install' })}
                      >
                        安装
                      </Button>
                    )}
                    {app.installed && app.status !== 'external' && (
                      <>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={action.isPending || app.status === 'running' || app.status === 'starting'}
                          onClick={() => action.mutate({ appId: app.app_id, kind: 'start' })}
                        >
                          启动
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={action.isPending || app.status !== 'running'}
                          onClick={() => action.mutate({ appId: app.app_id, kind: 'stop' })}
                        >
                          停止
                        </Button>
                      </>
                    )}
                    <Link
                      to="/external-apps/$appId"
                      params={{ appId: app.app_id }}
                      className="focus-visible:ring-ring inline-flex h-8 items-center rounded-md border px-3 text-sm focus-visible:ring-2 focus-visible:outline-none"
                    >
                      打开页面
                    </Link>
                  </div>
                </CardHeader>
              </Card>
            ))}
          </div>
        )}

        <SillyTavernDataSection />
      </div>
    </ScrollArea>
  )
}
