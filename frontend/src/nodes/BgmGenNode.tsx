import { memo } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'
import { runToNode, runNode } from '../engine/executor'

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

const DURATIONS = [15, 30, 60, 120, 240]

function BgmGenNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const updateNodeParams = useFlowStore((s) => s.updateNodeParams)
  const params = data.params
  const status = data.runtime.status
  const busy = status === 'queued' || status === 'running'
  const stale = status === 'stale'
  const instrumental = params.instrumental !== false

  return (
    <NodeShell type="bgm-gen" selected={selected} title={data.title}>
      <div style={{ padding: 10 }}>
        <div style={{ marginBottom: 6, fontSize: 11, color: 'var(--canvas-text-dim)' }}>
          连接风格描述文本；生成结果可进素材库或直接接合成节点
        </div>
        {fieldRow(
          '时长',
          <select
            className="nodrag"
            style={selectStyle}
            value={params.durationSeconds ?? 30}
            onChange={(e) => updateNodeParams(id, { durationSeconds: Number(e.target.value) })}
          >
            {DURATIONS.map((d) => (
              <option key={d} value={d}>
                {d} 秒
              </option>
            ))}
          </select>,
        )}
        <label className="nodrag" style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, fontSize: 12, color: 'var(--canvas-text-dim)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={instrumental}
            onChange={(e) => updateNodeParams(id, { instrumental: e.target.checked })}
          />
          纯音乐（无人声）
        </label>
        {!instrumental && (
          <textarea
            className="nodrag"
            value={params.lyrics ?? ''}
            placeholder="歌词（可选，支持 [verse] [chorus] 结构标记）"
            rows={2}
            onChange={(e) => updateNodeParams(id, { lyrics: e.target.value })}
            style={{
              width: '100%',
              background: 'var(--canvas-panel-2)',
              color: 'var(--canvas-text)',
              border: '1px solid var(--canvas-border)',
              borderRadius: 6,
              padding: '4px 6px',
              fontSize: 12,
              outline: 'none',
              resize: 'vertical',
              marginBottom: 6,
            }}
          />
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
          {busy ? '生成中…' : stale ? '重新生成（上游已变更）' : '生成 BGM'}
          <span style={{ fontSize: 11, opacity: 0.85 }}>本地 ACE-Step</span>
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
              重新生成（忽略缓存）
            </button>
          </div>
        )}
        {status === 'failed' && (
          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--canvas-danger)' }}>
            {data.runtime.error ?? 'BGM 生成失败'}
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const BgmGenNode = memo(BgmGenNodeInner)
