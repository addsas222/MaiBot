import { useQuery } from '@tanstack/react-query'

import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { getPluginWebuiPages } from '@/lib/plugin-api'
import { pluginWebuiPageRoute } from '@/router'

export function PluginWebuiPage() {
  const { pluginId } = pluginWebuiPageRoute.useParams()

  const pagesQuery = useQuery({
    queryKey: ['plugin-webui-pages'],
    queryFn: getPluginWebuiPages,
  })

  const plugin = pagesQuery.data?.find((item) => item.plugin_id === pluginId)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-w-0 px-4 pt-4 pb-3 sm:px-6 sm:pt-6">
        <h1 className="truncate text-xl font-bold sm:text-2xl">{plugin?.name ?? pluginId}</h1>
      </div>

      {pagesQuery.isPending ? (
        <div className="flex flex-1 items-center justify-center">
          <ThinkingIllustration size="sm" />
        </div>
      ) : pagesQuery.isError ? (
        <p className="text-muted-foreground flex-1 pt-10 text-center text-sm">
          {pagesQuery.error instanceof Error ? pagesQuery.error.message : '加载插件页面列表失败'}
        </p>
      ) : plugin ? (
        <iframe
          key={plugin.plugin_id}
          title={plugin.name}
          src={plugin.entry}
          className="min-h-0 w-full flex-1 border-0"
        />
      ) : (
        <p className="text-muted-foreground flex-1 pt-10 text-center text-sm">
          插件 {pluginId} 未提供 WebUI 页面
        </p>
      )}
    </div>
  )
}
