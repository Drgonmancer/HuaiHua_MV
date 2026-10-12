import type { NodeKind } from '../types'
import { NODE_TYPE_META } from '../nodes/NodeShell'

type MenuGroup = { heading: string; items: { type: NodeKind; description: string }[] }

// M1 先开放图工作流节点；视频/音频在 M2/M3 追加到这里即可
const MENU_GROUPS: MenuGroup[] = [
  {
    heading: '添加节点',
    items: [
      { type: 'prompt', description: '文本提示词，可 AI 生成' },
      { type: 'image-gen', description: '文生图 / 图生图' },
      { type: 'image-view', description: '展示图片结果' },
      { type: 'video-gen', description: '文生视频 / 图生视频' },
      { type: 'video-view', description: '展示视频结果' },
      { type: 'audio-gen', description: '文字转语音 (TTS)' },
      { type: 'audio-view', description: '播放音频结果' },
    ],
  },
  {
    heading: '添加资源',
    items: [{ type: 'asset', description: '上传参考图' }],
  },
  {
    heading: '辅助工具',
    items: [
      { type: 'compose', description: '视频+音轨 合成成片' },
      { type: 'group', description: '把相关节点框起来' },
    ],
  },
]

type AddNodeMenuProps = {
  screen: { x: number; y: number }
  onPick: (type: NodeKind) => void
  onClose: () => void
}

export function AddNodeMenu({ screen, onPick, onClose }: AddNodeMenuProps) {
  return (
    <>
      <div
        style={{ position: 'fixed', inset: 0, zIndex: 40 }}
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault()
          onClose()
        }}
      />
      <div
        style={{
          position: 'fixed',
          left: Math.min(screen.x, window.innerWidth - 240),
          top: Math.min(screen.y, window.innerHeight - 320),
          zIndex: 41,
          width: 224,
          background: 'var(--canvas-panel)',
          border: '1px solid var(--canvas-border)',
          borderRadius: 12,
          boxShadow: '0 12px 32px rgba(0,0,0,0.5)',
          padding: 6,
        }}
      >
        {MENU_GROUPS.map((group) => (
          <div key={group.heading}>
            <div
              style={{
                fontSize: 11,
                color: 'var(--canvas-text-dim)',
                padding: '6px 10px 2px',
              }}
            >
              {group.heading}
            </div>
            {group.items.map((item) => {
              const meta = NODE_TYPE_META[item.type]
              return (
                <button
                  key={item.type}
                  className="canvas-menu-item"
                  onClick={() => onPick(item.type)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    width: '100%',
                    padding: '7px 10px',
                    background: 'transparent',
                    border: 'none',
                    borderRadius: 8,
                    color: 'var(--canvas-text)',
                    fontSize: 13,
                    cursor: 'pointer',
                    textAlign: 'left',
                  }}
                >
                  <span style={{ color: meta.accent, width: 16, textAlign: 'center' }}>
                    {meta.icon}
                  </span>
                  <span style={{ flex: 1 }}>{meta.label}</span>
                  <span style={{ fontSize: 10, color: 'var(--canvas-text-dim)' }}>
                    {item.description}
                  </span>
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </>
  )
}
