import type { CSSProperties, ReactNode } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeKind } from '../types'
import { PORT_RULES } from './portRules'

export const NODE_TYPE_META: Record<
  NodeKind,
  { icon: string; label: string; accent: string }
> = {
  prompt: { icon: '≡', label: '提示词', accent: '#fbbf24' },
  asset: { icon: '🖼', label: '参考图', accent: '#34d399' },
  'image-gen': { icon: '✦', label: '文生图', accent: '#7c5cff' },
  'image-view': { icon: '📷', label: '图片视图', accent: '#60a5fa' },
  'video-gen': { icon: '🎬', label: '视频生成', accent: '#7c5cff' },
  'video-view': { icon: '▶', label: '视频视图', accent: '#60a5fa' },
  'audio-gen': { icon: '🎙', label: '音频生成', accent: '#7c5cff' },
  'audio-view': { icon: '🎵', label: '音频视图', accent: '#60a5fa' },
  compose: { icon: '⧉', label: '合成', accent: '#f472b6' },
  group: { icon: '▣', label: '分组', accent: '#9aa0aa' },
}

const PORT_LABELS: Record<string, string> = {
  'in:context': '上文',
  'in:prompt': '提示词',
  'in:ref': '参考图',
  'in:first': '首帧',
  'in:last': '末帧',
  'in:media': '输入',
  'in:text': '文本',
  'in:video': '视频',
  'in:audio': '音频',
}

type NodeShellProps = {
  type: NodeKind
  selected?: boolean
  width?: number
  children: ReactNode
  title?: string
  /** 不渲染输出 handle（如纯视图节点关闭输出时用） */
  hideSource?: boolean
}

/**
 * 节点卡片外壳：上方类型标签 + 深色圆角卡片 + 左侧输入端口（带标签）+
 * 右侧输出端口。端口从 PORT_RULES 自动生成，保证 UI 与规则一致。
 */
export function NodeShell({ type, selected, width = 260, children, hideSource, title }: NodeShellProps) {
  const meta = NODE_TYPE_META[type]
  const rule = PORT_RULES[type]
  const inputHandles = Object.keys(rule.inputs)

  const cardStyle: CSSProperties = {
    width,
    background: 'var(--canvas-panel)',
    border: `1.5px solid ${selected ? meta.accent : 'var(--canvas-border)'}`,
    borderRadius: 14,
    boxShadow: selected
      ? `0 0 0 3px ${meta.accent}33, 0 8px 24px rgba(0,0,0,0.4)`
      : '0 4px 16px rgba(0,0,0,0.3)',
    position: 'relative',
  }

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          marginBottom: 6,
          fontSize: 12,
          color: 'var(--canvas-text-dim)',
        }}
      >
        <span style={{ color: meta.accent }}>{meta.icon}</span>
        <span>{meta.label}{title && title !== meta.label ? ` · ${title}` : ''}</span>
      </div>
      <div style={cardStyle} className="canvas-node-card">
        {inputHandles.map((handleId, index) => {
          const top = `${((index + 1) / (inputHandles.length + 1)) * 100}%`
          return (
            <div key={handleId}>
              <Handle
                type="target"
                position={Position.Left}
                id={handleId}
                style={{
                  top,
                  width: 10,
                  height: 10,
                  background: 'var(--canvas-panel-2)',
                  border: '2px solid var(--canvas-text-dim)',
                }}
              />
              {inputHandles.length > 1 && (
                <span
                  style={{
                    position: 'absolute',
                    left: -6,
                    top,
                    transform: 'translate(-100%, -50%)',
                    fontSize: 10,
                    color: 'var(--canvas-text-dim)',
                    whiteSpace: 'nowrap',
                    pointerEvents: 'none',
                  }}
                >
                  {PORT_LABELS[handleId] ?? handleId}
                </span>
              )}
            </div>
          )
        })}
        {children}
        {!hideSource && (
          <Handle
            type="source"
            position={Position.Right}
            id="out"
            style={{
              width: 10,
              height: 10,
              background: meta.accent,
              border: '2px solid var(--canvas-panel)',
            }}
          />
        )}
      </div>
    </div>
  )
}
