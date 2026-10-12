import { create } from 'zustand'
import type { CanvasProject } from '../types'
import type { Production } from '../studioTypes'

export type SyncState = 'local' | 'syncing' | 'online' | 'offline'

export type Draft = {
  savedAt: string
  title: string
  nodes: unknown[]
  edges: unknown[]
  viewport: { x: number; y: number; zoom: number }
  production?: Production | null
}

type Conflict = { draft: Draft; server: CanvasProject }

type ProjectState = {
  projectId: string | null
  title: string
  syncState: SyncState
  lastSyncedAt: string | null
  loaded: boolean
  conflict: Conflict | null

  setProjectId: (id: string | null) => void
  setTitle: (title: string) => void
  setSyncState: (state: SyncState) => void
  setLastSyncedAt: (at: string | null) => void
  setLoaded: (loaded: boolean) => void
  setConflict: (conflict: Conflict | null) => void
}

export const useProjectStore = create<ProjectState>((set) => ({
  projectId: null,
  title: '未命名画布',
  syncState: 'local',
  lastSyncedAt: null,
  loaded: false,
  conflict: null,

  setProjectId: (projectId) => set({ projectId }),
  setTitle: (title) => set({ title }),
  setSyncState: (syncState) => set({ syncState }),
  setLastSyncedAt: (lastSyncedAt) => set({ lastSyncedAt }),
  setLoaded: (loaded) => set({ loaded }),
  setConflict: (conflict) => set({ conflict }),
}))
