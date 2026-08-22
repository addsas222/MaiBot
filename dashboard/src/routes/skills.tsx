import { useCallback } from 'react'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { useToast } from '@/hooks/use-toast'
import { getBotConfig, updateBotConfigSection } from '@/lib/config-api'
import { backendApi } from '@/lib/http'
import { BookOpen, Info } from 'lucide-react'

type SkillPreview = {
  name: string
  description: string
  version: string
  directory: string
  enabled: boolean
}

type SkillsListResponse = {
  success: boolean
  skills: SkillPreview[]
}

type ConfigSectionData = Record<string, unknown>

async function listSkills(): Promise<SkillPreview[]> {
  const data = await backendApi.get<SkillsListResponse>('/api/webui/skills', {
    cache: 'no-store',
    errorMessage: '获取技能列表失败',
  })
  return data.skills ?? []
}

async function loadSkillsConfig(): Promise<ConfigSectionData> {
  const config = await getBotConfig()
  const configPayload = config as { config?: Record<string, unknown> } & Record<string, unknown>
  const fullConfig = (configPayload.config ?? configPayload) as Record<string, unknown>
  return (fullConfig.skills ?? {}) as ConfigSectionData
}

export function SkillsPage() {
  return <SkillsPageContent />
}

function SkillsPageContent() {
  const { toast } = useToast()
  const queryClient = useQueryClient()

  // 技能列表：目录里实际扫描到的技能
  const skillsQuery = useQuery({
    queryKey: ['skills'],
    queryFn: listSkills,
  })

  // 当前技能配置：切换开关时在现有配置基础上更新
  const configQuery = useQuery({
    queryKey: ['skills-config'],
    queryFn: loadSkillsConfig,
  })

  const skillsEnabled = Boolean(configQuery.data?.enable ?? true)

  // 切换失败由全局 mutation 错误 toast 呈现
  const updateMutation = useMutation({
    mutationFn: async ({
      nextConfig,
      toastTitle,
    }: {
      nextConfig: ConfigSectionData
      toastTitle: string
    }) => {
      await updateBotConfigSection('skills', nextConfig)
      return toastTitle
    },
    onSuccess: (toastTitle) => {
      toast({ title: toastTitle })
      void queryClient.invalidateQueries({ queryKey: ['skills'] })
      void queryClient.invalidateQueries({ queryKey: ['skills-config'] })
    },
  })

  // 总开关：控制整个技能能力是否启用
  const toggleEnable = useCallback(
    (enabled: boolean) => {
      const currentConfig = configQuery.data ?? {}
      updateMutation.mutate({
        nextConfig: { ...currentConfig, enable: enabled },
        toastTitle: `技能${enabled ? '已启用' : '已禁用'}`,
      })
    },
    [configQuery.data, updateMutation]
  )

  // 单个技能开关：维护禁用名单
  const toggleSkill = useCallback(
    (skillName: string, enabled: boolean) => {
      const currentConfig = configQuery.data ?? {}
      const currentDisabled = Array.isArray(currentConfig.disabled_skills)
        ? (currentConfig.disabled_skills as string[])
        : []
      const nextDisabled = enabled
        ? currentDisabled.filter((name) => name !== skillName)
        : [...currentDisabled, skillName]
      updateMutation.mutate({
        nextConfig: { ...currentConfig, disabled_skills: nextDisabled },
        toastTitle: `${skillName}${enabled ? '已启用' : '已禁用'}`,
      })
    },
    [configQuery.data, updateMutation]
  )

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl md:text-3xl font-bold">技能</h1>
            <p className="text-muted-foreground mt-1 text-xs sm:text-sm">
              开启后技能会作为可按需加载的工具供麦麦在对话中自动调用
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="text-sm font-medium">启用技能</span>
            <Switch
              checked={skillsEnabled}
              disabled={updateMutation.isPending}
              aria-label="技能总开关"
              onCheckedChange={(checked) => toggleEnable(checked)}
            />
          </div>
        </div>

        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            技能目录内的每个子目录含一份带 frontmatter 的 SKILL.md 文件。调用技能工具后
            完整指令会注入模型上下文。
            {skillsQuery.data && (
              <span className="ml-1">当前共发现 {skillsQuery.data.length} 个技能。</span>
            )}
          </AlertDescription>
        </Alert>

        <Card>
          <CardHeader>
            <CardTitle>
              <span className="inline-flex items-center gap-2">
                <BookOpen className="h-4 w-4" />
                已发现的技能
              </span>
            </CardTitle>
            <CardDescription>以下技能来自配置的技能根目录</CardDescription>
          </CardHeader>
          <CardContent>
            {skillsQuery.isFetching ? (
              <div className="flex h-24 items-center justify-center">
                <ThinkingIllustration size="sm" />
              </div>
            ) : skillsQuery.data && skillsQuery.data.length > 0 ? (
              <ul className="divide-y">
                {skillsQuery.data.map((skill) => (
                  <li key={skill.name} className="py-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-sm font-medium">{skill.name}</span>
                          {skill.version && (
                            <Badge variant="secondary" className="font-mono text-xs">
                              v{skill.version}
                            </Badge>
                          )}
                          <Badge variant="outline" className="font-mono text-xs">
                            skill_{skill.name}
                          </Badge>
                        </div>
                        <p className="text-muted-foreground mt-1 text-sm">
                          {skill.description || '（无描述）'}
                        </p>
                        <p className="text-muted-foreground mt-1 font-mono text-xs">
                          {skill.directory}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Switch
                          checked={skill.enabled}
                          disabled={!skillsEnabled || updateMutation.isPending}
                          aria-label={`${skill.name} 开关`}
                          onCheckedChange={(checked) => toggleSkill(skill.name, checked)}
                        />
                        <span className="text-xs text-muted-foreground">
                          {!skillsEnabled ? '总开关已关闭' : skill.enabled ? '启用' : '禁用'}
                        </span>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground py-6 text-center text-sm">
                技能目录中暂未发现技能，请将 SKILL.md 放入配置的根目录。
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </ScrollArea>
  )
}
