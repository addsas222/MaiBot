import { useCallback, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useToast } from '@/hooks/use-toast'
import {
  addAdminUser,
  deleteAdminUser,
  listAdminUsers,
  listExternalEngines,
  testExternalEngine,
  type AdminUserEntry,
  type ExternalEngineItem,
} from '@/lib/admin-api'
import { Loader2, PlugZap, Server, ShieldCheck, Trash2, UserPlus } from 'lucide-react'

const CREATED_BY_LABELS: Record<string, string> = {
  PRESET: '出厂预设',
  PRESET_CURRENT: '初始管理员',
  MANUAL: '手动添加',
}

const ENGINE_KIND_LABELS: Record<string, string> = {
  http: '网络 HTTP',
  cli: '本机 CLI',
}

function createdByLabel(entry: AdminUserEntry): string {
  return CREATED_BY_LABELS[entry.created_by] ?? entry.created_by
}

function engineSummary(engine: ExternalEngineItem): string {
  if (engine.kind === 'http') {
    return [engine.base_url, engine.model].filter(Boolean).join(' · ')
  }
  return (engine.command ?? []).join(' ')
}

export function AdminUsersPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [userId, setUserId] = useState('')
  const [platform, setPlatform] = useState('qq')
  const [note, setNote] = useState('')
  const [lastTest, setLastTest] = useState<{
    engine: string
    elapsed_ms: number
    preview: string
  } | null>(null)

  const listQuery = useQuery({ queryKey: ['admin-users'], queryFn: listAdminUsers })
  const enginesQuery = useQuery({ queryKey: ['external-engines'], queryFn: listExternalEngines })

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
  }, [queryClient])

  const addMutation = useMutation({
    mutationFn: () => addAdminUser({ user_id: userId.trim(), platform: platform.trim(), note }),
    onSuccess: (result) => {
      toast({
        title: '已添加管理员',
        description: `${result.item.user_id}（${result.item.platform || '*'}）`,
      })
      setUserId('')
      setNote('')
      invalidate()
    },
    onError: (error: Error) => {
      toast({ title: '添加失败', description: error.message, variant: 'destructive' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (entry: AdminUserEntry) => deleteAdminUser(entry.user_id, entry.platform),
    onSuccess: (_result, entry) => {
      toast({ title: '已移除管理员', description: `${entry.user_id}（${entry.platform || '*'}）` })
      invalidate()
    },
    onError: (error: Error) => {
      toast({ title: '移除失败', description: error.message, variant: 'destructive' })
    },
  })

  const testMutation = useMutation({
    mutationFn: (engine: ExternalEngineItem) =>
      testExternalEngine(engine.name, '连通性测试：请直接回复 pong'),
    onSuccess: (result) => {
      setLastTest({ engine: result.engine, elapsed_ms: result.elapsed_ms, preview: result.preview })
    },
    onError: (error: Error) => {
      toast({ title: '探活失败', description: error.message, variant: 'destructive' })
      setLastTest(null)
    },
  })

  const items = listQuery.data?.items ?? []
  const engines = enginesQuery.data?.items ?? []
  const enginesEnabled = enginesQuery.data?.enable ?? false
  const submitting = addMutation.isPending || deleteMutation.isPending

  return (
    <div className="container mx-auto space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" />
            管理员管理
          </CardTitle>
          <CardDescription>
            维护运行时动态管理员列表；该列表对聊天指令 /admin 与 /agent 生效。至少保留一名管理员。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-56"
              placeholder="用户 ID"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
            />
            <Input
              className="w-32"
              placeholder="平台（默认 qq）"
              value={platform}
              onChange={(event) => setPlatform(event.target.value)}
            />
            <Input
              className="w-64"
              placeholder="备注（可选）"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
            <Button disabled={!userId.trim() || submitting} onClick={() => addMutation.mutate()}>
              {addMutation.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : (
                <UserPlus className="mr-1 h-4 w-4" />
              )}
              添加
            </Button>
          </div>

          {listQuery.isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>平台</TableHead>
                  <TableHead>用户 ID</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>备注</TableHead>
                  <TableHead className="w-16 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                      暂无管理员条目
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((entry) => (
                    <TableRow key={`${entry.platform}:${entry.user_id}`}>
                      <TableCell>{entry.platform || '*'}</TableCell>
                      <TableCell className="font-mono">{entry.user_id}</TableCell>
                      <TableCell>
                        <Badge variant={entry.created_by === 'MANUAL' ? 'secondary' : 'outline'}>
                          {createdByLabel(entry)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{entry.note}</TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={submitting}
                          onClick={() => deleteMutation.mutate(entry)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Server className="h-5 w-5" />
            外置引擎
          </CardTitle>
          <CardDescription>
            本机可部署（CLI 子进程）与网络（OpenAI 兼容 HTTP）外置 Agent 的统一视图；
            新增与编辑请在配置页 external_agent 段完成。
            {!enginesEnabled && ' 当前功能未启用（external_agent.enable）。'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {enginesQuery.isLoading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : engines.length === 0 ? (
            <div className="py-6 text-center text-muted-foreground">
              尚未配置任何外置引擎
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead>接入信息</TableHead>
                  <TableHead>超时</TableHead>
                  <TableHead className="w-24 text-right">探活</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {engines.map((engine) => (
                  <TableRow key={engine.name}>
                    <TableCell className="font-medium">{engine.name}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{ENGINE_KIND_LABELS[engine.kind] ?? engine.kind}</Badge>
                    </TableCell>
                    <TableCell className="max-w-md truncate font-mono text-xs text-muted-foreground">
                      {engineSummary(engine) || '-'}
                    </TableCell>
                    <TableCell>{engine.timeout_seconds}s</TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={!enginesEnabled || testMutation.isPending}
                        onClick={() => testMutation.mutate(engine)}
                      >
                        {testMutation.isPending && testMutation.variables?.name === engine.name ? (
                          <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        ) : (
                          <PlugZap className="mr-1 h-4 w-4" />
                        )}
                        测试
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {lastTest && (
            <Alert>
              <AlertDescription>
                <span className="font-medium">{lastTest.engine}</span> 探活成功，耗时{' '}
                {lastTest.elapsed_ms}ms，输出 {lastTest.preview.length} 字符：
                <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted p-2 text-xs">
                  {lastTest.preview}
                </pre>
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
