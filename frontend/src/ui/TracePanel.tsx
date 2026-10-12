import { useShallow } from 'zustand/react/shallow'
import { useFlowStore } from '../store/flowStore'
import { NODE_TYPE_META } from '../nodes/NodeShell'
import { GEN_NODE_TYPES } from '../engine/graph'
import type { NodeKind, RunStatus } from '../types'

const STATUS_LABELS: Record<RunStatus, { text: string; color: string }> = {
  idle: { text: '待运行', color: 'var(--canvas-text-dim)' },
  queued: { text: '排队中', color: 'var(--canvas-warning)' },
  running: { text: '生成中', color: 'var(--canvas-accent-2)' },
  succeeded: { text: '已完成', color: 'var(--canvas-success)' },
  failed: { text: '失败', color: 'var(--canvas-danger)' },
  canceled: { text: '已取消', color: 'var(--canvas-text-dim)' },
  stale: { text: '已过期', color: 'var(--canvas-warning)' },
}

function duration(startedAt?: string, finishedAt?: string): string {
  if (!startedAt) return ''
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now()
  const seconds = Math.max(0, Math.round((end - new Date(startedAt).getTime()) / 1000))
  return `${seconds}s`
}

/** Pro Mode 执行轨迹：直接从节点 runtime 派生（模型/积分/状态/耗时） */
export function TracePanel({ onClose }: { onClose: () => void }) {
  // useShallow：filter 每次返回新数组，浅比较元素引用避免 useSyncExternalStore 死循环
  const genNodes = useFlowStore(
    useShallow((s) => s.nodes.filter((n) => GEN_NODE_TYPES.has(n.type ?? ''))),
  )

  return (
    <div className="canvas-trace-panel" data-testid="trace-panel">
      <div className="canvas-trace-header">
        <span>执行轨迹 · {genNodes.length} 个制作节点</span>
        <button onClick={onClose}>×</button>
      </div>
      <div className="canvas-trace-body">
        {genNodes.map((node) => {
          const meta = NODE_TYPE_META[node.type as NodeKind]
          const runtime = node.data.runtime
          const label = STATUS_LABELS[runtime.status]
          return (
            <div key={node.id} className="canvas-trace-row">
              <span style={{ color: meta.accent, width: 18 }}>{meta.icon}</span>
              <span className="canvas-trace-title">{node.data.title}</span>
              <span className="canvas-trace-model">{node.data.params.model ?? ''}</span>
              <span style={{ color: label.color }}>{label.text}</span>
              <span className="canvas-trace-meta">
                {runtime.startedAt ? duration(runtime.startedAt, runtime.finishedAt) : ''}
              </span>
              {runtime.error && (
                <span style={{ color: 'var(--canvas-danger)', fontSize: 11 }}>
                  {runtime.error}
                </span>
              )}
            </div>
          )
        })}
        {!genNodes.length && (
          <div style={{ padding: 12, color: 'var(--canvas-text-dim)', fontSize: 12 }}>
            画布上还没有生成节点
          </div>
        )}
      </div>
    </div>
  )
}
