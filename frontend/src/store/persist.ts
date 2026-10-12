/**
 * 本地草稿 + 后端防抖同步（复刻参考截图顶部状态条语义）：
 * - 任何图变更 → 300ms 防抖写 localStorage（"内容会先保存在本地"）
 * - 3s 防抖 PUT 到后端（"待后端确认后完成同步"），失败指数退避 3s→30s
 * - beforeunload 同步 flush 本地草稿
 * - 打开项目时比较 本地 savedAt vs 服务器 updated_at，本地更新则提示二选一
 */
import { useFlowStore } from './flowStore'
import { useProjectStore, type Draft } from './projectStore'
import { api, ApiError } from '../api/client'
import { getBootstrap } from '../bootstrap'
import type { CanvasNode, CanvasEdge, CanvasProject, Viewport } from '../types'
import { useProductionStore } from './productionStore'

const DRAFT_PREFIX = 'canvas-draft:'

function draftKey(projectId: string | null): string {
  return `${DRAFT_PREFIX}${projectId ?? 'new'}`
}

/** 序列化时剔除瞬态字段；running 落库为 queued（加载时凭 jobId 恢复轮询） */
function serializeGraph(): Draft {
  const { nodes, edges, viewport } = useFlowStore.getState()
  const { title } = useProjectStore.getState()
  return {
    savedAt: new Date().toISOString(),
    title,
    production: useProductionStore.getState().production,
    viewport,
    nodes: nodes.map((node) => {
      const { selected, dragging, measured, ...rest } = node
      void selected
      void dragging
      void measured
      const runtime = { ...rest.data.runtime }
      if (runtime.status === 'running') runtime.status = 'queued'
      return { ...rest, data: { ...rest.data, runtime } }
    }),
    edges: edges.map((edge) => {
      const { selected, ...rest } = edge
      void selected
      return rest
    }),
  }
}

function writeDraft(savedAt?: string) {
  const { projectId } = useProjectStore.getState()
  try {
    const draft = serializeGraph()
    if (savedAt) draft.savedAt = savedAt
    localStorage.setItem(draftKey(projectId), JSON.stringify(draft))
  } catch {
    // localStorage 满/不可用时静默跳过（后端同步仍在）
  }
}

function readDraft(projectId: string | null): Draft | null {
  try {
    const raw = localStorage.getItem(draftKey(projectId))
    return raw ? (JSON.parse(raw) as Draft) : null
  } catch {
    return null
  }
}

function clearDraft(projectId: string | null) {
  try {
    localStorage.removeItem(draftKey(projectId))
  } catch {
    // ignore
  }
}

let localTimer: number | null = null
let remoteTimer: number | null = null
let backoffMs = 3000
let syncInFlight = false
const syncWaiters: (() => void)[] = []
let loginWarned = false

function scheduleLocalSave() {
  if (localTimer) window.clearTimeout(localTimer)
  localTimer = window.setTimeout(() => {
    localTimer = null
    writeDraft()
    const project = useProjectStore.getState()
    if (project.syncState === 'online') project.setSyncState('local')
  }, 300)
}

function scheduleRemoteSync(delay = 3000) {
  if (remoteTimer) window.clearTimeout(remoteTimer)
  remoteTimer = window.setTimeout(() => {
    remoteTimer = null
    void syncNow()
  }, delay)
}

export async function syncNow(): Promise<void> {
  if (syncInFlight) {
    await new Promise<void>((resolve) => syncWaiters.push(resolve))
    return syncNow()
  }
  const project = useProjectStore.getState()
  if (localTimer) { window.clearTimeout(localTimer); localTimer = null }
  if (remoteTimer) { window.clearTimeout(remoteTimer); remoteTimer = null }

  if (!navigator.onLine) {
    project.setSyncState('offline')
    scheduleRemoteSync(backoffMs)
    backoffMs = Math.min(backoffMs * 2, 30000)
    return
  }

  syncInFlight = true
  project.setSyncState('syncing')
  const draft = serializeGraph()
  const revision = useFlowStore.getState().revision
  const production = useProductionStore.getState().production
  try {
    const path = project.projectId
      ? `/api/canvas/projects/${project.projectId}`
      : '/api/canvas/projects'
    const result = await api<{ project: CanvasProject }>(path, {
      method: project.projectId ? 'PUT' : 'POST',
      body: JSON.stringify({
        title: draft.title,
        nodes: draft.nodes,
        edges: draft.edges,
        viewport: draft.viewport,
        production: draft.production,
      }),
    })
    const saved = result.project
    if (!project.projectId && saved?.id) {
      // 首次保存拿到 id：迁移草稿键 + 更新地址栏
      clearDraft(null)
      project.setProjectId(saved.id)
      window.history.replaceState(null, '', `/tools/canvas/${saved.id}`)
    }
    const unchanged = useFlowStore.getState().revision === revision
      && useProjectStore.getState().title === draft.title
      && useProductionStore.getState().production === production
      && JSON.stringify(useFlowStore.getState().viewport) === JSON.stringify(draft.viewport)
    writeDraft(unchanged ? saved.updated_at : undefined)
    project.setSyncState(unchanged ? 'online' : 'local')
    project.setLastSyncedAt(saved?.updated_at ?? new Date().toISOString())
    if (!unchanged) scheduleRemoteSync(250)
    backoffMs = 3000
  } catch (error) {
    project.setSyncState('offline')
    if (error instanceof ApiError && error.status === 401) {
      if (!loginWarned) {
        loginWarned = true
        useFlowStore.getState().pushToast('未登录：内容仅保存在本地', 'error')
      }
      // 未登录时不重试轰炸后端；恢复由用户登录后刷新触发
    } else {
      scheduleRemoteSync(backoffMs)
      backoffMs = Math.min(backoffMs * 2, 30000)
    }
  } finally {
    syncInFlight = false
    syncWaiters.splice(0).forEach((resolve) => resolve())
  }
}

function applyDraft(draft: Draft) {
  useProductionStore.getState().setProduction(draft.production ?? null)
  useProjectStore.getState().setTitle(draft.title || '未命名画布')
  useFlowStore
    .getState()
    .setGraph(
      (draft.nodes ?? []) as CanvasNode[],
      (draft.edges ?? []) as CanvasEdge[],
      (draft.viewport ?? { x: 0, y: 0, zoom: 1 }) as Viewport,
    )
}

function applyServer(project: CanvasProject) {
  useProductionStore.getState().setProduction(project.production ?? null)
  useProjectStore.getState().setTitle(project.title || '未命名画布')
  useFlowStore.getState().setGraph(project.nodes ?? [], project.edges ?? [], project.viewport)
}

export function resolveConflict(choice: 'local' | 'server') {
  const project = useProjectStore.getState()
  const conflict = project.conflict
  if (!conflict) return
  if (choice === 'server') {
    applyServer(conflict.server)
    clearDraft(project.projectId)
  } else {
    applyDraft(conflict.draft)
    scheduleRemoteSync(500)
  }
  project.setConflict(null)
}

export async function loadProject(): Promise<void> {
  const bootstrap = getBootstrap()
  const project = useProjectStore.getState()
  const projectId = bootstrap.project_id

  if (projectId) {
    project.setProjectId(projectId)
    const draft = readDraft(projectId)
    try {
      const result = await api<{ project: CanvasProject }>(`/api/canvas/projects/${projectId}`)
      const server = result.project
      if (draft && draft.savedAt > (server.updated_at ?? '')) {
        // 本地草稿更新：先展示草稿，弹条允许切回服务器版本
        applyDraft(draft)
        project.setConflict({ draft, server })
      } else {
        applyServer(server)
        writeDraft(server.updated_at)
      }
      project.setSyncState('online')
      project.setLastSyncedAt(server.updated_at ?? null)
    } catch {
      if (draft) {
        applyDraft(draft)
        useFlowStore.getState().pushToast('服务器不可达，已恢复本地草稿', 'info')
      }
      project.setSyncState('offline')
    }
  } else {
    const draft = readDraft(null)
    if (draft) {
      applyDraft(draft)
      useFlowStore.getState().pushToast('已恢复未保存的本地草稿', 'info')
    }
  }
  project.setLoaded(true)
}

/** App 挂载时调用一次。返回清理函数。 */
export function initPersistence(): () => void {
  void loadProject()

  let lastRevision = useFlowStore.getState().revision
  let lastViewport = useFlowStore.getState().viewport
  const unsubFlow = useFlowStore.subscribe((state) => {
    if (!useProjectStore.getState().loaded) return
    if (state.revision !== lastRevision || state.viewport !== lastViewport) {
      lastRevision = state.revision
      lastViewport = state.viewport
      scheduleLocalSave()
      scheduleRemoteSync()
    }
  })

  let lastTitle = useProjectStore.getState().title
  const unsubProject = useProjectStore.subscribe((state) => {
    if (!state.loaded) return
    if (state.title !== lastTitle) {
      lastTitle = state.title
      scheduleLocalSave()
      scheduleRemoteSync()
    }
  })

  const unsubProduction = useProductionStore.subscribe((state, previous) => {
    if (state.production !== previous.production && useProjectStore.getState().loaded) {
      scheduleLocalSave()
      scheduleRemoteSync()
    }
  })

  const flushDraft = () => writeDraft()
  const onOnline = () => scheduleRemoteSync(500)
  const onOffline = () => useProjectStore.getState().setSyncState('offline')
  window.addEventListener('beforeunload', flushDraft)
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)

  return () => {
    unsubFlow()
    unsubProject()
    unsubProduction()
    window.removeEventListener('beforeunload', flushDraft)
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
    if (localTimer) window.clearTimeout(localTimer)
    if (remoteTimer) window.clearTimeout(remoteTimer)
  }
}
