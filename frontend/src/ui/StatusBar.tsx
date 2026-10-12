import { useEffect, useRef } from 'react'
import { useFlowStore } from '../store/flowStore'
import { useProjectStore } from '../store/projectStore'
import { resolveConflict } from '../store/persist'
import { BrandIdentity, PersonalSignature } from './BrandIdentity'
import { useProductionStore } from '../store/productionStore'

const SYNC_LABELS: Record<string, { text: string; color: string }> = {
  local: { text: '本地草稿 · 内容会先保存在本地，待后端确认后完成同步', color: 'var(--canvas-warning)' },
  syncing: { text: '同步中…', color: 'var(--canvas-text-dim)' },
  online: { text: '在线 · 已同步', color: 'var(--canvas-success)' },
  offline: { text: '离线 · 仅本地保存，恢复后自动同步', color: 'var(--canvas-danger)' },
}

export function StatusBar({ onOpenStudio }: { onOpenStudio: () => void }) {
  const headerRef = useRef<HTMLElement>(null)
  const title = useProjectStore((s) => s.title)
  const setTitle = useProjectStore((s) => s.setTitle)
  const syncState = useProjectStore((s) => s.syncState)
  const conflict = useProjectStore((s) => s.conflict)
  const nodeCount = useFlowStore((s) => s.nodes.length)
  const label = SYNC_LABELS[syncState]
  const production = useProductionStore((s) => s.production)
  const kindLabel = production?.kind === 'comic' ? '漫剧' : production?.kind === 'product' ? '商品宣传片' : '创作项目'
  useEffect(() => {
    const header = headerRef.current
    const shell = header?.closest<HTMLElement>('.canvas-shell')
    if (!header || !shell) return
    const measure = () => shell.style.setProperty('--canvas-header-clearance', `${header.offsetTop + header.offsetHeight + 12}px`)
    const observer = new ResizeObserver(measure)
    observer.observe(header)
    measure()
    return () => observer.disconnect()
  }, [])

  return (
    <>
      <header ref={headerRef} className="canvas-topbar" data-testid="canvas-statusbar">
        <BrandIdentity />
        <div className="canvas-project-info">
          <div className="canvas-project-line">
            <button className="canvas-studio-entry" onClick={onOpenStudio} aria-label="打开创作工作台">
              {kindLabel}<span aria-hidden="true"> ↗</span>
            </button>
            <label className="canvas-project-label" htmlFor="canvas-project-title">作品</label>
            <input
              id="canvas-project-title"
              className="canvas-title-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={80}
              aria-label="画布标题"
            />
            <span className="canvas-sync-status" style={{ color: label.color }} data-testid="sync-state" data-sync={syncState} title={label.text}>
              {label.text}
            </span>
          </div>
          <span className="canvas-topbar-hint">商品宣传片 / 漫剧 · {nodeCount} 节点 · 双击添加 · Shift 框选 · Ctrl+D 复制</span>
        </div>
        <PersonalSignature />
      </header>

      {conflict && (
        <div className="canvas-conflict-banner" data-testid="conflict-banner">
          <span>检测到更新的本地草稿（{new Date(conflict.draft.savedAt).toLocaleString()}）</span>
          <button onClick={() => resolveConflict('local')}>保留本地草稿</button>
          <button className="secondary" onClick={() => resolveConflict('server')}>
            用服务器版本
          </button>
        </div>
      )}
    </>
  )
}
