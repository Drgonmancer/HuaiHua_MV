import { memo } from 'react'
import { NodeResizer, type NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'

function GroupNodeInner({ data, selected }: NodeProps<CanvasNode>) {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        borderRadius: 16,
        border: '1.5px dashed rgba(255,255,255,0.18)',
        background: 'rgba(255,255,255,0.03)',
      }}
    >
      <NodeResizer
        isVisible={selected}
        minWidth={240}
        minHeight={180}
        lineStyle={{ borderColor: 'var(--canvas-accent)' }}
        handleStyle={{ background: 'var(--canvas-accent)' }}
      />
      <div
        style={{
          position: 'absolute',
          top: -22,
          left: 8,
          fontSize: 12,
          color: 'var(--canvas-text-dim)',
        }}
      >
        ▣ {data.title}
      </div>
    </div>
  )
}

export const GroupNode = memo(GroupNodeInner)
