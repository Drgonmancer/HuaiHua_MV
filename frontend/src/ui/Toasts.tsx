import { useFlowStore } from '../store/flowStore'

const TONE_COLORS = {
  info: 'var(--canvas-text-dim)',
  error: 'var(--canvas-danger)',
  success: 'var(--canvas-success)',
} as const

export function Toasts() {
  const toasts = useFlowStore((s) => s.toasts)
  if (!toasts.length) return null

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 24,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 50,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        alignItems: 'center',
      }}
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          style={{
            background: 'var(--canvas-panel)',
            border: `1px solid ${TONE_COLORS[toast.tone]}`,
            color: TONE_COLORS[toast.tone],
            borderRadius: 10,
            padding: '8px 16px',
            fontSize: 13,
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
          }}
        >
          {toast.message}
        </div>
      ))}
    </div>
  )
}
