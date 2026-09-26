import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CoreSettings } from './CoreSettings'
import type { ComponentProps } from 'react'

const botSection = {
  nickname: '麦麦',
}

const personalitySection = {
  behavior_style: '先观察，再行动。',
  multiple_probability: 0,
  personality: '你是一个温和的人。',
  reply_style: '说话简短自然。',
}

/** 页面内嵌管理员管理面板依赖 react-query；测试里补上 Provider（应用内由全局 Provider 提供） */
function renderCoreSettings(props: ComponentProps<typeof CoreSettings>) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <CoreSettings {...props} />
    </QueryClientProvider>
  )
}

describe('CoreSettings', () => {
  it('醒目展示三个核心字段及其运行阶段', () => {
    renderCoreSettings({
      botSection,
      personalitySection,
      onPersonalitySectionChange: vi.fn(),
    })

    expect(screen.getByRole('heading', { name: '麦麦' })).toBeInTheDocument()
    expect(screen.getByText('说话 · replyer')).toBeInTheDocument()
    expect(screen.getByText('行动 · planner')).toBeInTheDocument()
    expect(screen.getByLabelText('人格配置')).toHaveValue('你是一个温和的人。')
    expect(screen.getByLabelText('表达方式')).toHaveValue('说话简短自然。')
    expect(screen.getByLabelText('行为风格')).toHaveValue('先观察，再行动。')

    for (const fieldName of ['人格配置', '表达方式', '行为风格']) {
      expect(screen.getByLabelText(fieldName)).toHaveClass('min-h-40', 'resize-y')
      expect(screen.getByLabelText(fieldName)).not.toHaveClass('resize-none')
    }
  })

  it('编辑字段时保留同一配置节的其他值', () => {
    const handleChange = vi.fn()
    renderCoreSettings({
      botSection,
      personalitySection,
      onPersonalitySectionChange: handleChange,
    })

    fireEvent.change(screen.getByLabelText('行为风格'), {
      target: { value: '只在适合的时候参与。' },
    })

    expect(handleChange).toHaveBeenCalledWith({
      ...personalitySection,
      behavior_style: '只在适合的时候参与。',
    })
  })

  it('旧配置缺少行为风格时沿用原人格且页面仍可编辑', () => {
    const handleChange = vi.fn()
    const legacyPersonalitySection = {
      multiple_probability: 0,
      personality: '升级前 Planner 也使用这段人格配置。',
      reply_style: '说话简短自然。',
    }

    renderCoreSettings({
      botSection,
      personalitySection: legacyPersonalitySection,
      onPersonalitySectionChange: handleChange,
    })

    expect(screen.getByLabelText('行为风格')).toHaveValue('升级前 Planner 也使用这段人格配置。')

    fireEvent.change(screen.getByLabelText('表达方式'), {
      target: { value: '新的表达方式。' },
    })

    expect(handleChange).toHaveBeenCalledWith({
      ...legacyPersonalitySection,
      behavior_style: '升级前 Planner 也使用这段人格配置。',
      reply_style: '新的表达方式。',
    })
  })

  it('行为风格存在但类型错误时继续暴露配置问题', () => {
    expect(() =>
      renderCoreSettings({
        botSection,
        personalitySection: { ...personalitySection, behavior_style: [] },
        onPersonalitySectionChange: vi.fn(),
      })
    ).toThrow('核心设置字段 behavior_style 必须是字符串')
  })
})
