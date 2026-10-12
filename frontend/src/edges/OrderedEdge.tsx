import { memo } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'
import type { CanvasEdge, MediaKind } from '../types'
import { useFlowStore } from '../store/flowStore'

const MEDIA_COLORS: Record<MediaKind, string> = {
  text: '#fbbf24',
  image: '#34d399',
  video: '#60a5fa',
  audio: '#f472b6',
}

function OrderedEdgeInner({
  id,
  target,
  targetHandleId,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selected,
  data,
}: EdgeProps<CanvasEdge>) {
  const cycleEdgeOrder = useFlowStore((s) => s.cycleEdgeOrder)
  const siblingCount = useFlowStore(
    (s) =>
      s.edges.filter(
        (e) => e.target === target && (e.targetHandle ?? null) === (targetHandleId ?? null),
      ).length,
  )

  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  })

  const color = MEDIA_COLORS[data?.mediaType ?? 'text']

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: selected ? color : 'rgba(255,255,255,0.35)',
          strokeWidth: selected ? 2 : 1.5,
        }}
      />
      {siblingCount > 1 && (
        <EdgeLabelRenderer>
          <button
            className="nodrag nopan"
            title={`第 ${data?.order ?? 1} 个输入，点击换序`}
            onClick={(e) => {
              e.stopPropagation()
              cycleEdgeOrder(id)
            }}
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              width: 20,
              height: 20,
              borderRadius: '50%',
              border: `1.5px solid ${color}`,
              background: 'var(--canvas-panel)',
              color,
              fontSize: 11,
              fontWeight: 700,
              lineHeight: 1,
              cursor: 'pointer',
              pointerEvents: 'all',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {data?.order ?? 1}
          </button>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

export const OrderedEdge = memo(OrderedEdgeInner)
export { MEDIA_COLORS }
