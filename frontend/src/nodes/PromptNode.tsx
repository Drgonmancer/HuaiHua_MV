import { memo, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'
import { api, ApiError } from '../api/client'
import { useProductionStore } from '../store/productionStore'
import { useProjectStore } from '../store/projectStore'

function PromptNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const updateNodeValue = useFlowStore((s) => s.updateNodeValue)
  const pushToast = useFlowStore((s) => s.pushToast)
  const [generating, setGenerating] = useState(false)

  const handleAssist = async () => {
    const instruction = (data.value?.text ?? '').trim()
    if (instruction.length < 4) {
      pushToast('请先输入至少 4 个字的生成要求', 'error')
      return
    }
    setGenerating(true)
    try {
      const result = await api<{ text: string; credits_cost: number }>(
        '/api/canvas/prompt-assist',
        { method: 'POST', body: JSON.stringify({ instruction, production: useProductionStore.getState().production, project_id: useProjectStore.getState().projectId }) },
      )
      updateNodeValue(id, { ...data.value, text: result.text })
      pushToast('提示词已生成', 'success')
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : 'AI 生成失败，请稍后重试'
      pushToast(message, 'error')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <NodeShell type="prompt" selected={selected} title={data.title}>
      <div style={{ padding: 10 }}>
        <textarea
          className="nodrag nowheel"
          value={data.value?.text ?? ''}
          placeholder="输入提示词，或引用上游文本…"
          onChange={(e) => updateNodeValue(id, { ...data.value, text: e.target.value })}
          style={{
            width: '100%',
            minHeight: 88,
            resize: 'vertical',
            background: 'var(--canvas-panel-2)',
            color: 'var(--canvas-text)',
            border: '1px solid var(--canvas-border)',
            borderRadius: 8,
            padding: 8,
            fontSize: 13,
            fontFamily: 'inherit',
            outline: 'none',
          }}
        />
        <button
          className="nodrag"
          disabled={generating}
          onClick={() => void handleAssist()}
          style={{
            marginTop: 6,
            padding: '5px 10px',
            borderRadius: 6,
            border: '1px solid var(--canvas-border)',
            background: 'var(--canvas-panel-2)',
            color: generating ? 'var(--canvas-text-dim)' : 'var(--canvas-text)',
            fontSize: 12,
            cursor: generating ? 'wait' : 'pointer',
          }}
        >
          {generating ? '生成中…' : '✨ AI 生成'}
        </button>
      </div>
    </NodeShell>
  )
}

export const PromptNode = memo(PromptNodeInner)
