import { memo, useEffect, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'
import { runToNode, collectInputs } from '../engine/executor'
import { getAudioMeta, type AudioMeta } from '../api/voices'

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

function AudioGenNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const updateNodeParams = useFlowStore((s) => s.updateNodeParams)
  const [meta, setMeta] = useState<AudioMeta | null>(null)
  useEffect(() => {
    void getAudioMeta().then(setMeta)
  }, [])

  const params = data.params
  const status = data.runtime.status
  const busy = status === 'queued' || status === 'running'
  const stale = status === 'stale'

  // 上游文本字符数（随连线/上游文本变化实时重算），用于按字符预估
  const revision = useFlowStore((s) => s.revision)
  const charCount = (() => {
    void revision
    const inputs = collectInputs(id)
    return (inputs.text ?? []).join('').length
  })()

  const models = meta?.models ?? []
  const modelId = models.some((model) => model.id === params.model) ? params.model! : models[0]?.id ?? ''
  useEffect(() => {
    if (modelId && modelId !== params.model) updateNodeParams(id, { model: modelId })
  }, [id, modelId, params.model, updateNodeParams])

  return (
    <NodeShell type="audio-gen" selected={selected} title={data.title}>
      <div style={{ padding: 10 }}>
        {fieldRow(
          '模型',
          <select
            className="nodrag"
            style={selectStyle}
            value={modelId}
            onChange={(e) => updateNodeParams(id, { model: e.target.value })}
          >
            {models.length ? (
              models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))
            ) : (
              <option value="">配音服务未配置</option>
            )}
          </select>,
        )}
        {fieldRow(
          '音色',
          <select
            className="nodrag"
            style={selectStyle}
            value={params.voiceId ?? ''}
            onChange={(e) => updateNodeParams(id, { voiceId: e.target.value })}
          >
            <option value="">{meta ? '请选择音色' : '加载音色中…'}</option>
            {(meta?.voices ?? []).map((voice) => (
              <option key={voice.id} value={voice.id}>
                {voice.label}
              </option>
            ))}
          </select>,
        )}
        {fieldRow(
          '语速',
          <select
            className="nodrag"
            style={selectStyle}
            value={params.speed ?? 1}
            onChange={(e) => updateNodeParams(id, { speed: Number(e.target.value) })}
          >
            {[0.75, 1, 1.25, 1.5].map((s) => (
              <option key={s} value={s}>
                {s}×
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
          <span style={{ fontSize: 11, opacity: 0.85 }}>
            {charCount > 0 ? `${charCount} 字 · 按供应商计费` : '连接台词文本'}
          </span>
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

export const AudioGenNode = memo(AudioGenNodeInner)
