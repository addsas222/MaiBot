import { useCallback, useState } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

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
  type AdminUserEntry,
} from '@/lib/admin-api'
import { Loader2, ShieldCheck, Trash2, UserPlus } from 'lucide-react'

const CREATED_BY_LABELS: Record<string, string> = {
  PRESET: '出厂预设',
  PRESET_CURRENT: '初始管理员',
  MANUAL: '手动添加',
}

function createdByLabel(entry: AdminUserEntry): string {
  return CREATED_BY_LABELS[entry.created_by] ?? entry.created_by
}

export function AdminUsersPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [userId, setUserId] = useState('')
  const [platform, setPlatform] = useState('qq')
  const [note, setNote] = useState('')

  const listQuery = useQuery({ queryKey: ['admin-users'], queryFn: listAdminUsers })

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

  const items = listQuery.data?.items ?? []
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

</div>
  )
}
