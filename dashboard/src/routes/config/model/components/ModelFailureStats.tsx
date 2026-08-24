/**
 * 模型故障率统计面板
 *
 * 消费 /api/webui/models/failure-stats（LLM 请求快照聚合），
 * 在模型管理页顶部展示各模型的重试后成功/最终失败与失败率，
 * 帮助识别需要调整降级链或冷却策略的模型。
 */
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'

import { getModelFailureStats } from '@/lib/config-api'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

function formatFailRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

export function ModelFailureStats() {
  const statsQuery = useQuery({
    queryKey: ['model-failure-stats'],
    queryFn: () => getModelFailureStats(),
    staleTime: 60_000,
    retry: false,
  })

  if (statsQuery.isLoading || statsQuery.isError) {
    return null
  }

  const models = (statsQuery.data?.models ?? []).filter(
    (entry) => entry.total > 0 && entry.fail_rate > 0,
  )

  if (models.length === 0) {
    return null
  }

  return (
    <Card className="rounded-2xl border-border/70 bg-card/90 shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <AlertTriangle className="h-4 w-4 text-amber-500" />
          模型故障率
        </CardTitle>
        <CardDescription>
          基于 LLM 请求快照聚合（共扫描 {statsQuery.data?.scanned ?? 0} 条），仅展示存在最终失败的模型。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>模型</TableHead>
              <TableHead className="text-right">快照数</TableHead>
              <TableHead className="text-right">重试后成功</TableHead>
              <TableHead className="text-right">最终失败</TableHead>
              <TableHead className="text-right">
                <Badge variant={models.some((m) => m.fail_rate >= 0.5) ? 'destructive' : 'secondary'}>
                  失败率
                </Badge>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {models.map((entry) => (
              <TableRow key={entry.model}>
                <TableCell className="font-mono text-xs">{entry.model}</TableCell>
                <TableCell className="text-right">{entry.total}</TableCell>
                <TableCell className="text-right">{entry.retried_ok}</TableCell>
                <TableCell className="text-right">{entry.final_failed}</TableCell>
                <TableCell className="text-right font-semibold">
                  {formatFailRate(entry.fail_rate)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}
