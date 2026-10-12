import type { Node, Edge } from '@xyflow/react'
import type { Production } from './studioTypes'

export type MediaKind = 'text' | 'image' | 'video' | 'audio'

export type NodeKind =
  | 'prompt'
  | 'asset'
  | 'image-gen'
  | 'image-view'
  | 'video-gen'
  | 'video-view'
  | 'audio-gen'
  | 'audio-view'
  | 'compose'
  | 'group'

export type RunStatus =
  | 'idle'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled'
  | 'stale'

export type NodeOutput = {
  kind: MediaKind
  url?: string
  text?: string
  meta?: Record<string, unknown>
}

export type NodeRuntime = {
  status: RunStatus
  jobKind?: 'image' | 'video' | 'audio' | 'compose'
  jobId?: string
  jobIds?: string[]
  clientRunId?: string
  error?: string
  creditsCost?: number
  startedAt?: string
  finishedAt?: string
}

export type NodeParams = {
  model?: string
  ratio?: string
  size?: string
  count?: number
  durationSeconds?: number
  resolution?: string
  useLastFrame?: boolean
  mode?: 'tts' | 'bgm' | 'sfx'
  voiceId?: string
  speed?: number
  emotion?: string
}

export type CanvasNodeData = {
  title: string
  mediaType?: Exclude<MediaKind, 'text'>
  value?: { url?: string; text?: string }
  params: NodeParams
  outputs?: NodeOutput[]
  selectedOutput?: number
  runtime: NodeRuntime
  [key: string]: unknown
}

export type CanvasNode = Node<CanvasNodeData>

export type CanvasEdgeData = {
  order: number
  mediaType: MediaKind
  [key: string]: unknown
}

export type CanvasEdge = Edge<CanvasEdgeData>

export type Viewport = { x: number; y: number; zoom: number }

export type CanvasProject = {
  id: string
  title: string
  schema_version: number
  nodes: CanvasNode[]
  edges: CanvasEdge[]
  viewport: Viewport
  created_at?: string
  updated_at?: string
  production?: Production | null
}
