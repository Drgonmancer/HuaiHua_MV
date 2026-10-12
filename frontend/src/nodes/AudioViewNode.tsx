import { memo } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'

function AudioViewNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const upstreamOutputs = useFlowStore((s) => {
    const edge = s.edges.find((e) => e.target === id && e.targetHandle === 'in:media')
    const source = edge ? s.nodes.find((n) => n.id === edge.source) : undefined
    return source?.data.outputs
  })
  const outputs = (upstreamOutputs ?? data.outputs ?? []).filter(
    (o) => o.kind === 'audio' && o.url,
  )
  const current = outputs[Math.min(data.selectedOutput ?? 0, Math.max(outputs.length - 1, 0))]
  // fake 模式产物是 SVG 占位图
  const isImagePlaceholder = current?.url?.startsWith('data:image/')

  return (
    <NodeShell type="audio-view" selected={selected} title={data.title} width={260}>
      <div style={{ padding: 10 }}>
        {current?.url ? (
          isImagePlaceholder ? (
            <img
              src={current.url}
              alt={data.title}
              style={{ width: '100%', display: 'block', borderRadius: 8 }}
            />
          ) : (
            <div>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  marginBottom: 8,
                  color: 'var(--canvas-text-dim)',
                  fontSize: 12,
                }}
              >
                <span style={{ fontSize: 20 }}>🎵</span>
                <span>语音已生成</span>
              </div>
              <audio
                className="nodrag"
                src={current.url}
                controls
                preload="metadata"
                style={{ width: '100%' }}
              />
            </div>
          )
        ) : (
          <div
            style={{
              minHeight: 80,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--canvas-text-dim)',
              fontSize: 12,
            }}
          >
            等待上游生成音频…
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const AudioViewNode = memo(AudioViewNodeInner)
