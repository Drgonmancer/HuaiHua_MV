import { memo } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { runToNode, runNode } from '../engine/executor'

function ComposeNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const status = data.runtime.status
  const busy = status === 'queued' || status === 'running'
  const stale = status === 'stale'

  return (
    <NodeShell type="compose" selected={selected} title={data.title} width={220}>
      <div style={{ padding: 10 }}>
        <div style={{ fontSize: 12, color: 'var(--canvas-text-dim)', marginBottom: 8 }}>
          连接视频与音频，在本地合成镜头
        </div>
        <button
          className="nodrag canvas-generate-btn"
          disabled={busy}
          data-node-id={id}
          onClick={() => void runToNode(id)}
          style={{
            width: '100%',
            padding: '8px 12px',
            borderRadius: 8,
            border: 'none',
            background: busy ? 'var(--canvas-panel-2)' : 'var(--canvas-accent)',
            color: busy ? 'var(--canvas-text-dim)' : '#fff',
            fontSize: 13,
            fontWeight: 600,
            cursor: busy ? 'default' : 'pointer',
          }}
        >
          {busy ? '合成中…' : stale ? '重新合成（上游已变更）' : '开始合成'}
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
              重新合成（忽略缓存）
            </button>
          </div>
        )}
        {status === 'failed' && (
          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--canvas-danger)' }}>
            {data.runtime.error ?? '合成失败'}
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const ComposeNode = memo(ComposeNodeInner)
