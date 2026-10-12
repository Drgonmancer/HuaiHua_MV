import { memo } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'

function VideoViewNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const upstreamOutputs = useFlowStore((s) => {
    const edge = s.edges.find((e) => e.target === id && e.targetHandle === 'in:media')
    const source = edge ? s.nodes.find((n) => n.id === edge.source) : undefined
    return source?.data.outputs
  })
  const outputs = (upstreamOutputs ?? data.outputs ?? []).filter(
    (o) => o.kind === 'video' && o.url,
  )
  const current = outputs[Math.min(data.selectedOutput ?? 0, Math.max(outputs.length - 1, 0))]
  const poster = (current?.meta as { poster_url?: string } | undefined)?.poster_url
  // fake 模式产物是 SVG 占位图，用 img 渲染
  const isImagePlaceholder = current?.url?.startsWith('data:image/')

  return (
    <NodeShell type="video-view" selected={selected} title={data.title} width={280}>
      <div style={{ padding: 8 }}>
        <div
          style={{
            minHeight: 158,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'var(--canvas-panel-2)',
            borderRadius: 10,
            overflow: 'hidden',
          }}
        >
          {current?.url ? (
            isImagePlaceholder ? (
              <img src={current.url} alt={data.title} style={{ width: '100%', display: 'block' }} />
            ) : (
              <video
                className="nodrag"
                src={current.url}
                poster={poster || undefined}
                controls
                preload="metadata"
                style={{ width: '100%', display: 'block' }}
              />
            )
          ) : (
            <div style={{ color: 'var(--canvas-text-dim)', fontSize: 12, padding: 24 }}>
              等待上游生成视频…
            </div>
          )}
        </div>
      </div>
    </NodeShell>
  )
}

export const VideoViewNode = memo(VideoViewNodeInner)
