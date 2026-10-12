import type { CanvasEdge, CanvasNode, CanvasProject, NodeOutput } from './types'

export type ProductionKind = 'product' | 'comic'
export type ProductionCharacter = {
  id: string; name: string; description: string; reference_asset_ids: string[]; voice_id: string
}
export type ProductionScene = {
  id: string; name: string; description: string; reference_asset_ids: string[]
}
export type ProductionEpisode = { id: string; title: string; synopsis: string; script: string }
export type ProductionShot = {
  id: string; episode_id: string; title: string; description: string
  image_prompt: string; video_prompt: string; dialogue: string
  character_ids: string[]; scene_id: string; duration_seconds: number; order: number
  node_ids?: {
    prompt?: string; image?: string; image_view?: string; video?: string; video_view?: string
    audio?: string; audio_view?: string; compose?: string
  }
}
export type TimelineClip = {
  shot_id: string; order: number; trim_start: number; trim_end: number; subtitle: string
}
export type Production = {
  kind: ProductionKind; brief: string; style: string; aspect_ratio: string; duration_seconds: number
  script: string; characters: ProductionCharacter[]; scenes: ProductionScene[]
  episodes: ProductionEpisode[]; shots: ProductionShot[]; timeline: TimelineClip[]
  planning_source?: 'template' | 'ai'
}
export type StudioProject = CanvasProject & { production?: Production | null }
export type StudioAsset = {
  id: string; name: string; category: string; media_type: 'image' | 'video' | 'audio'
  url: string; created_at?: string
}
export type StudioTemplate = {
  id: string; kind: ProductionKind; name: string; description: string; steps: string[]
}
export type StudioJob = {
  id: string; kind: string; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled'
  node_id?: string; project_id?: string; error?: string; outputs?: NodeOutput[]
  created_at?: string; updated_at?: string
}
export type StudioSettings = {
  providers: Record<'text' | 'image' | 'video' | 'audio', { configured: boolean; model: string }>
  ffmpeg: { available: boolean }; config_path: string
}
export type StoryboardResult = {
  production: Production; nodes: CanvasNode[]; edges: CanvasEdge[]; title: string
}
export type ExportClip = { url: string; trim_start: number; trim_end: number; subtitle: string }
export type ExportRequest = {
  project_id: string; clips: ExportClip[]; audio_urls: string[]
  aspect_ratio: string; resolution: string; fps: number
}
