import { memo, useEffect, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'
import { runToNode } from '../engine/executor'
import { getImageModels, type ImageModel } from '../api/imageModels'

const RATIOS = ['1:1', '4:3', '3:4', '16:9', '9:16']

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

function ImageGenNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const updateNodeParams = useFlowStore((s) => s.updateNodeParams)
  const [models, setModels] = useState<ImageModel[]>([])
  useEffect(() => { void getImageModels().then(setModels) }, [])
  const params = data.params
  const model = models.find((m) => m.id === params.model) ?? models[0]
  useEffect(() => {
    if (model && model.id !== params.model) updateNodeParams(id, { model: model.id })
  }, [id, model, params.model, updateNodeParams])
  const count = params.count ?? 1
  const status = data.runtime.status
  const busy = status === 'queued' || status === 'running'
  const stale = status === 'stale'

  return (
    <NodeShell type="image-gen" selected={selected} title={data.title}>
      <div style={{ padding: 10 }}>
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
        {fieldRow(
          '比例',
          <select
            className="nodrag"
            style={selectStyle}
            value={params.ratio ?? '1:1'}
            onChange={(e) => updateNodeParams(id, { ratio: e.target.value })}
          >
            {RATIOS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>,
        )}
        {fieldRow(
          '数量',
          <select
            className="nodrag"
            style={selectStyle}
            value={count}
            onChange={(e) => updateNodeParams(id, { count: Number(e.target.value) })}
          >
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
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
          {busy ? '生成中…' : stale ? '重新生成（上游已变更）' : '开始生成'}
          <span style={{ fontSize: 11, opacity: 0.85 }}>按供应商计费</span>
        </button>
        {status === 'failed' && (
          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--canvas-danger)' }}>
            {data.runtime.error ?? '生成失败'}
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const ImageGenNode = memo(ImageGenNodeInner)
