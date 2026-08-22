import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { getExternalApp } from '@/lib/external-apps-api'
import { externalAppPageRoute } from '@/router'

export function ExternalAppPage() {
  const { appId } = externalAppPageRoute.useParams()
  const appQuery = useQuery({
    queryKey: ['external-apps', appId],
    queryFn: () => getExternalApp(appId),
  })
  const app = appQuery.data

  // iframe 直连本地端口，跨源探测不可靠；以健康轮询结果驱动加载态超时提示
  const [loaded, setLoaded] = useState(false)
  const [timedOut, setTimedOut] = useState(false)
  useEffect(() => {
    if (loaded) return
    const timer = setTimeout(() => setTimedOut(true), 8000)
    return () => clearTimeout(timer)
  }, [loaded])

  if (appQuery.isPending) {
    return (
      <div className="flex h-full items-center justify-center">
        <ThinkingIllustration size="sm" />
      </div>
    )
  }
  if (appQuery.isError || !app) {
    return (
      <p className="text-muted-foreground flex h-full items-center justify-center text-sm">
        加载外部应用失败
      </p>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-w-0 items-center gap-3 px-4 pt-4 pb-3 sm:px-6 sm:pt-6">
        <h1 className="truncate text-xl font-bold sm:text-2xl">{app.name}</h1>
        {!loaded && timedOut && (
          <span className="text-muted-foreground truncate text-xs">
            未检测到页面响应——请确认应用已启动、端口可访问，且未开启拦截跨域框架的浏览器扩展
          </span>
        )}
      </div>
      <iframe
        key={appId}
        title={app.name}
        src={app.embed_url ?? `http://127.0.0.1:${app.port}/`}
        onLoad={() => setLoaded(true)}
        className="min-h-0 w-full flex-1 border-0"
      />
    </div>
  )
}
