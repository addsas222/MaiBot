import { useCallback, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { useToast } from '@/hooks/use-toast'
import {
  getEnginesConfig,
  listExternalEngines,
  saveEnginesConfig,
} from '@/lib/admin-api'
import { Loader2, PlugZap, Plus, Server, Trash2 } from 'lucide-react'

export function ExternalEnginesPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const runtimeQuery = useQuery({ queryKey: ['external-engines'], queryFn: listExternalEngines })
  const configQuery = useQuery({ queryKey: ['engines-config'], queryFn: getEnginesConfig })

  const [cliList, setCliList] = useState<Array<{ name: string; command: string; timeout_seconds: number }>>([])
  const [httpList, setHttpList] = useState<Array<{ name: string; base_url: string; api_key: string; model: string; timeout_seconds: number }>>([])
  const [dirty, setDirty] = useState(false)
  const initRef = useCallback((data: any) => {
    setCliList(data.cli.map((e: any) => ({ ...e, command: (e.command || []).join(' ') })))
    setHttpList(data.http.map((e: any) => ({ ...e })))
    setDirty(false)
  }, [])
  const [initialized, setInitialized] = useState(false)
  if (!initialized && configQuery.data) {
    initRef(configQuery.data)
    setInitialized(true)
  }

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['external-engines'] })
    void queryClient.invalidateQueries({ queryKey: ['engines-config'] })
  }, [queryClient])

  const saveMutation = useMutation({
    mutationFn: () => saveEnginesConfig({
      cli: cliList.map(e => ({
        name: e.name, command: e.command.trim().split(/\s+/),
        working_dir: '', timeout_seconds: e.timeout_seconds, max_output_chars: 4000,
      })),
      http: httpList.map(e => ({
        name: e.name, base_url: e.base_url, api_key: '',
        model: '', system_prompt: '', timeout_seconds: e.timeout_seconds,
      })),
    }),
    onSuccess: () => { toast({ title: '引擎配置已保存' }); invalidate(); setDirty(false) },
    onError: (err: Error) => { toast({ title: '保存失败', description: err.message, variant: 'destructive' }) },
  })

  const testMutation = useMutation({
    mutationFn: (name: string) => import('@/lib/admin-api').then(m => m.testExternalEngine(name, 'ping')),
    onSuccess: (r) => { toast({ title: `${r.engine} 探活成功`, description: `${r.elapsed_ms}ms` }) },
    onError: (err: Error) => { toast({ title: '探活失败', description: err.message, variant: 'destructive' }) },
  })

  const enabled = configQuery.data?.enable ?? false

  return (
    <div className="container mx-auto space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Server className="h-5 w-5" /> 外置引擎</CardTitle>
          <CardDescription>
            管理本机 CLI 与网络 HTTP AI Agent 引擎；支持自定义接入任意 OpenAI 兼容服务和本机 CLI 工具。
            {!enabled && ' 当前功能未启用（external_agent.enable = false）。'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* CLI 引擎表 */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-medium">CLI 引擎（本机子进程）</h3>
              <Button size="sm" variant="outline" disabled={!enabled}
                onClick={() => { setCliList(l => [...l, { name: `new-cli-${Date.now() % 1000}`, command: '/usr/bin/env echo', timeout_seconds: 300 }]); setDirty(true) }}>
                <Plus className="mr-1 h-3 w-3" /> 添加 CLI
              </Button>
            </div>
            <Table>
              <TableHeader><TableRow><TableHead>名称</TableHead><TableHead>命令</TableHead><TableHead>超时(s)</TableHead><TableHead className="w-10" /></TableRow></TableHeader>
              <TableBody>
                {cliList.map((e, i) => (
                  <TableRow key={i}>
                    <TableCell><Input value={e.name} onChange={ev => { setCliList(l => l.map((x,j)=>j===i?{...x,name:ev.target.value}:x)); setDirty(true) }} /></TableCell>
                    <TableCell><Input value={e.command} onChange={ev => { setCliList(l => l.map((x,j)=>j===i?{...x,command:ev.target.value}:x)); setDirty(true) }} /></TableCell>
                    <TableCell><Input type="number" value={e.timeout_seconds} onChange={ev => { setCliList(l => l.map((x,j)=>j===i?{...x,timeout_seconds:+ev.target.value}:x)); setDirty(true) }} className="w-20" /></TableCell>
                    <TableCell><Button size="sm" variant="ghost" onClick={() => { setCliList(l => l.filter((_,j)=>j!==i)); setDirty(true) }}><Trash2 className="h-4 w-4" /></Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* HTTP 引擎表 */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-medium">HTTP 引擎（OpenAI 兼容）</h3>
              <Button size="sm" variant="outline" disabled={!enabled}
                onClick={() => { setHttpList(l => [...l, { name: `new-http-${Date.now() % 1000}`, base_url: 'https://', api_key: '', model: '', timeout_seconds: 120 }]); setDirty(true) }}>
                <Plus className="mr-1 h-3 w-3" /> 添加 HTTP
              </Button>
            </div>
            <Table>
              <TableHeader><TableRow><TableHead>名称</TableHead><TableHead>Base URL</TableHead><TableHead>模型</TableHead><TableHead>超时(s)</TableHead><TableHead className="w-10" /></TableRow></TableHeader>
              <TableBody>
                {httpList.map((e, i) => (
                  <TableRow key={i}>
                    <TableCell><Input value={e.name} onChange={ev => { setHttpList(l => l.map((x,j)=>j===i?{...x,name:ev.target.value}:x)); setDirty(true) }} /></TableCell>
                    <TableCell><Input value={e.base_url} onChange={ev => { setHttpList(l => l.map((x,j)=>j===i?{...x,base_url:ev.target.value}:x)); setDirty(true) }} /></TableCell>
                    <TableCell><Input value={e.model} onChange={ev => { setHttpList(l => l.map((x,j)=>j===i?{...x,model:ev.target.value}:x)); setDirty(true) }} /></TableCell>
                    <TableCell><Input type="number" value={e.timeout_seconds} onChange={ev => { setHttpList(l => l.map((x,j)=>j===i?{...x,timeout_seconds:+ev.target.value}:x)); setDirty(true) }} className="w-20" /></TableCell>
                    <TableCell><Button size="sm" variant="ghost" onClick={() => { setHttpList(l => l.filter((_,j)=>j!==i)); setDirty(true) }}><Trash2 className="h-4 w-4" /></Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* 操作栏 */}
          <div className="flex items-center gap-2">
            <Button disabled={!dirty || !enabled || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}>
              {saveMutation.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
              保存配置
            </Button>
            {dirty && <Badge variant="secondary">有未保存更改</Badge>}
          </div>
        </CardContent>
      </Card>

      {/* 运行时探活 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><PlugZap className="h-4 w-4" /> 探活测试</CardTitle>
        </CardHeader>
        <CardContent>
          {runtimeQuery.isLoading ? (
            <Loader2 className="animate-spin" />
          ) : (runtimeQuery.data?.items ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">无已加载的引擎</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {(runtimeQuery.data?.items ?? []).map(e => (
                <Button key={e.name} size="sm" variant="outline"
                  disabled={!enabled || testMutation.isPending}
                  onClick={() => testMutation.mutate(e.name)}>
                  {e.name}
                </Button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
