import { memo } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'
import { runToNode, runNode } from '../engine/executor'

function LipsyncNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  useFlowStore((s) => s.revision) // 保持与其它生成节点一致的响应式行为
  const status = data.runtime.status
  const busy = status === 'queued' || status === 'running'
  const stale = status === 'stale'

  return (
    <NodeShell type="lipsync" selected={selected} title={data.title} width={230}>
      <div style={{ padding: 10 }}>
        <div style={{ marginBottom: 8, fontSize: 11, color: 'var(--canvas-text-dim)' }}>
          连接视频与人声，本地服务把口型对齐到人物（支持漫剧/卡通形象）
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
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
          }}
        >
          {busy ? '对口型中…' : stale ? '重新对齐（上游已变更）' : '开始对口型'}
          <span style={{ fontSize: 11, opacity: 0.85 }}>本地服务</span>
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
              重新对齐（忽略缓存）
            </button>
          </div>
        )}
        {status === 'failed' && (
          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--canvas-danger)' }}>
            {data.runtime.error ?? '对口型失败'}
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const LipsyncNode = memo(LipsyncNodeInner)
