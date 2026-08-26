import { useCallback, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
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
  listExternalEngines,
  testExternalEngine,
} from '@/lib/admin-api'
import { Loader2, PlugZap, Server } from 'lucide-react'

const KIND_LABELS: Record<string, string> = { http: '网络 HTTP', cli: '本机 CLI' }

export function ExternalEnginesPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [testingName, setTestingName] = useState<string | null>(null)

  const enginesQuery = useQuery({ queryKey: ['external-engines'], queryFn: listExternalEngines })
  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['external-engines'] })
  }, [queryClient])

  const testMutation = useMutation({
    mutationFn: (name: string) => testExternalEngine(name, '连通性测试：请直接回复 pong'),
    onSuccess: (r) => {
      toast({ title: `${r.engine} 探活成功`, description: `耗时 ${r.elapsed_ms}ms` })
      invalidate()
    },
    onError: (err: Error) => {
      toast({ title: '探活失败', description: err.message, variant: 'destructive' })
    },
  })

  const items = enginesQuery.data?.items ?? []
  const enabled = enginesQuery.data?.enable ?? false

  return (
    <div className="container mx-auto space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Server className="h-5 w-5" />
            外置引擎
          </CardTitle>
          <CardDescription>
            管理本机 CLI 与网络 HTTP 外置 AI Agent 引擎；新增/编辑请在配置页 external_agent 段操作。
            {!enabled && ' 当前功能未启用。'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {enginesQuery.isLoading ? (
            <div className="flex justify-center py-8"><Loader2 className="animate-spin" /></div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow><TableHead>名称</TableHead><TableHead>类型</TableHead><TableHead>超时</TableHead><TableHead className="text-right">探活</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {items.map(e => (
                  <TableRow key={e.name}>
                    <TableCell className="font-medium">{e.name}</TableCell>
                    <TableCell><Badge variant="outline">{KIND_LABELS[e.kind] ?? e.kind}</Badge></TableCell>
                    <TableCell>{e.timeout_seconds}s</TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="outline" disabled={!enabled || testMutation.isPending}
                        onClick={() => { setTestingName(e.name); testMutation.mutate(e.name) }}>
                        {testMutation.isPending && testingName === e.name
                          ? <Loader2 className="h-4 w-4 animate-spin" />
                          : <PlugZap className="h-4 w-4" />}
                        测试
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
