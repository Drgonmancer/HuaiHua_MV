import { memo } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'

function ImageViewNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const updateNodeData = useFlowStore((s) => s.updateNodeData)
  // 实时读取上游（生成/素材）节点的产物；无连线时退回自身快照
  const upstreamOutputs = useFlowStore((s) => {
    const edge = s.edges.find((e) => e.target === id && e.targetHandle === 'in:media')
    const source = edge ? s.nodes.find((n) => n.id === edge.source) : undefined
    return source?.data.outputs
  })
  const outputs = (upstreamOutputs ?? data.outputs ?? []).filter(
    (o) => o.kind === 'image' && o.url,
  )
  const selectedIndex = Math.min(data.selectedOutput ?? 0, Math.max(outputs.length - 1, 0))
  const current = outputs[selectedIndex]

  return (
    <NodeShell type="image-view" selected={selected} title={data.title} width={240}>
      <div style={{ padding: 8 }}>
        <div
          style={{
            minHeight: 160,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'var(--canvas-panel-2)',
            borderRadius: 10,
            overflow: 'hidden',
          }}
        >
          {current?.url ? (
            <img
              src={current.url}
              alt={data.title}
              loading="lazy"
              style={{ width: '100%', display: 'block' }}
            />
          ) : (
            <div style={{ color: 'var(--canvas-text-dim)', fontSize: 12, padding: 24 }}>
              等待上游生成结果…
            </div>
          )}
        </div>
        {outputs.length > 1 && (
          <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
            {outputs.map((output, index) => (
              <img
                key={index}
                src={output.url}
                alt={`${index + 1}`}
                className="nodrag"
                onClick={() => updateNodeData(id, { selectedOutput: index })}
                style={{
                  width: 40,
                  height: 40,
                  objectFit: 'cover',
                  borderRadius: 6,
                  cursor: 'pointer',
                  border:
                    index === selectedIndex
                      ? '2px solid var(--canvas-accent)'
                      : '2px solid transparent',
                }}
              />
            ))}
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const ImageViewNode = memo(ImageViewNodeInner)
