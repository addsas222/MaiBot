import { Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'

import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { getPluginWebuiPages } from '@/lib/plugin-api'
import { AppWindow } from 'lucide-react'

export function PluginWebuiPagesIndexPage() {
  const pagesQuery = useQuery({
    queryKey: ['plugin-webui-pages'],
    queryFn: getPluginWebuiPages,
  })

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-4 sm:space-y-6 sm:p-6">
        <div className="min-w-0">
          <h1 className="text-xl font-bold sm:text-2xl md:text-3xl">插件页面</h1>
          <p className="text-muted-foreground mt-1 text-xs sm:text-sm">
            插件目录下存在 webui/index.html 时，其页面会自动出现在这里
          </p>
        </div>

        {pagesQuery.isPending ? (
          <div className="flex h-40 items-center justify-center">
            <ThinkingIllustration size="sm" />
          </div>
        ) : pagesQuery.isError ? (
          <p className="text-muted-foreground py-10 text-center text-sm">
            {pagesQuery.error instanceof Error ? pagesQuery.error.message : '加载插件页面列表失败'}
          </p>
        ) : pagesQuery.data && pagesQuery.data.length > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {pagesQuery.data.map((plugin) => (
              <Link
                key={plugin.plugin_id}
                to="/plugin-pages/$pluginId"
                params={{ pluginId: plugin.plugin_id }}
                className="focus-visible:ring-ring rounded-xl focus-visible:ring-2 focus-visible:outline-none"
              >
                <Card className="hover:border-primary/50 h-full transition-colors">
                  <CardHeader>
                    <CardTitle>
                      <span className="inline-flex items-center gap-2">
                        <AppWindow className="h-4 w-4 shrink-0" />
                        <span className="min-w-0 break-all">{plugin.name}</span>
                      </span>
                    </CardTitle>
                    <CardDescription>{plugin.description || '（无描述）'}</CardDescription>
                  </CardHeader>
                </Card>
              </Link>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground py-10 text-center text-sm">
            暂无插件提供 WebUI 页面
          </p>
        )}
      </div>
    </ScrollArea>
  )
}
