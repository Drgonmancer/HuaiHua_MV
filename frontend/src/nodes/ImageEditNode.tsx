import { memo, useEffect, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'
import { runToNode, runNode } from '../engine/executor'
import { getImageModels, type ImageModel } from '../api/imageModels'

function fieldRow(label: string, control: React.ReactNode) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        marginBottom: 6,
      }}
    >
      <span style={{ fontSize: 12, color: 'var(--canvas-text-dim)', flexShrink: 0 }}>{label}</span>
      {control}
    </div>
  )
}

const selectStyle: React.CSSProperties = {
  background: 'var(--canvas-panel-2)',
  color: 'var(--canvas-text)',
  border: '1px solid var(--canvas-border)',
  borderRadius: 6,
  padding: '4px 6px',
  fontSize: 12,
  outline: 'none',
  maxWidth: 150,
}

function ImageEditNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const updateNodeParams = useFlowStore((s) => s.updateNodeParams)
  const [models, setModels] = useState<ImageModel[]>([])
  useEffect(() => { void getImageModels().then(setModels) }, [])
  const params = data.params
  const model = models.find((m) => m.id === params.model) ?? models[0]
  useEffect(() => {
    if (model && model.id !== params.model) updateNodeParams(id, { model: model.id })
  }, [id, model, params.model, updateNodeParams])
  const status = data.runtime.status
  const busy = status === 'queued' || status === 'running'
  const stale = status === 'stale'

  return (
    <NodeShell type="image-edit" selected={selected} title={data.title}>
      <div style={{ padding: 10 }}>
        <div style={{ marginBottom: 6, fontSize: 11, color: 'var(--canvas-text-dim)' }}>
          连接待修图片与编辑指令；参考图端口可接角色形象锁定参考
        </div>
        {fieldRow(
          '模型',
          <select
            className="nodrag"
            style={selectStyle}
            value={model?.id ?? ''}
            onChange={(e) => updateNodeParams(id, { model: e.target.value })}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
            {!models.length && <option value="">图片服务未配置</option>}
          </select>,
        )}
        <button
          className="nodrag canvas-generate-btn"
          disabled={busy}
          data-node-id={id}
          onClick={() => void runToNode(id)}
          style={{
            width: '100%',
            marginTop: 8,
            padding: '8px 12px',
            borderRadius: 8,
            border: 'none',
            background: busy ? 'var(--canvas-panel-2)' : 'var(--canvas-accent)',
            color: busy ? 'var(--canvas-text-dim)' : '#fff',
            fontSize: 13,
            fontWeight: 600,
            cursor: busy ? 'default' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
          }}
        >
          {busy ? '修图中…' : stale ? '重新修图（上游已变更）' : '开始修图'}
          <span style={{ fontSize: 11, opacity: 0.85 }}>按供应商计费</span>
        </button>
        {status === 'succeeded' && (
          <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
            <span style={{ fontSize: 11, color: 'var(--canvas-text-dim)' }}>
              {data.runtime.cached ? '已缓存 · 未重新计费' : '已完成'}
            </span>
            <button
              className="nodrag"
              data-node-id={id}
              onClick={() => runNode(id, { force: true })}
              style={{
                fontSize: 11, color: 'var(--canvas-text)', background: 'var(--canvas-panel-2)',
                border: '1px solid var(--canvas-border)', borderRadius: 6, padding: '3px 8px', cursor: 'pointer',
              }}
            >
              重新修图（忽略缓存）
            </button>
          </div>
        )}
        {status === 'failed' && (
          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--canvas-danger)' }}>
            {data.runtime.error ?? '修图失败'}
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const ImageEditNode = memo(ImageEditNodeInner)
