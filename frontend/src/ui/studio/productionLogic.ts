import type { ExportRequest, Production, StoryboardResult, StudioJob, TimelineClip } from '../../studioTypes'
import type { CanvasEdge, CanvasNode, NodeOutput } from '../../types'

export function synchronizeTimeline(production: Production): TimelineClip[] {
  const byShot = new Map(production.timeline.map((clip) => [clip.shot_id, clip]))
  return [...production.shots].sort((a, b) => a.order - b.order).map((shot, index) => {
    const saved = byShot.get(shot.id)
    const duration = Number.isFinite(shot.duration_seconds) && shot.duration_seconds > 0
      ? shot.duration_seconds : 4
    const trimEnd = Math.min(duration, saved && saved.trim_end > 0 ? saved.trim_end : duration)
    const trimStart = Math.min(Math.max(0, saved?.trim_start ?? 0), Math.max(0, trimEnd - 0.1))
    return {
      shot_id: shot.id, order: index + 1, trim_start: trimStart, trim_end: trimEnd,
      subtitle: saved?.subtitle ?? shot.dialogue,
    }
  })
}

export function reorderShots(production: Production, shotId: string, direction: -1 | 1): Production {
  const shots = [...production.shots].sort((a, b) => a.order - b.order)
  const index = shots.findIndex((shot) => shot.id === shotId)
  const target = index + direction
  if (index < 0 || target < 0 || target >= shots.length) return production
  ;[shots[index], shots[target]] = [shots[target], shots[index]]
  const next = { ...production, shots: shots.map((shot, i) => ({ ...shot, order: i + 1 })) }
  return { ...next, timeline: synchronizeTimeline(next) }
}

export function prepareStoryboardFragment(result: StoryboardResult, existing: CanvasNode[]): StoryboardResult {
  const offsetX = existing.length ? Math.max(...existing.map((node) => node.position.x)) + 520 : 80
  const occupied = new Set(existing.map((node) => node.id))
  const idMap = new Map<string, string>()
  const fragmentId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
  for (const [index, node] of result.nodes.entries()) {
    let id = node.id
    if (occupied.has(id)) id = `studio-${fragmentId}-${index}`
    occupied.add(id)
    idMap.set(node.id, id)
  }
  return {
    ...result,
    nodes: result.nodes.map((node) => ({
      ...node, id: idMap.get(node.id)!,
      parentId: node.parentId ? idMap.get(node.parentId) : undefined,
      position: { x: node.position.x + (node.parentId ? 0 : offsetX), y: node.position.y + (node.parentId ? 0 : 60) },
    })),
    edges: result.edges.map((edge, index) => ({
      ...edge, id: `studio-edge-${fragmentId}-${index}`,
      source: idMap.get(edge.source) ?? edge.source, target: idMap.get(edge.target) ?? edge.target,
    })),
    production: {
      ...result.production,
      shots: result.production.shots.map((shot) => ({
        ...shot,
        node_ids: shot.node_ids
          ? Object.fromEntries(Object.entries(shot.node_ids).map(([key, id]) => [key, idMap.get(id) ?? id]))
          : undefined,
      })),
    },
  }
}

export function mediaForNode(
  nodeId: string | undefined, kind: 'video' | 'audio' | 'image',
  nodes: CanvasNode[], edges: CanvasEdge[], visited = new Set<string>(),
): NodeOutput | undefined {
  if (!nodeId || visited.has(nodeId)) return undefined
  visited.add(nodeId)
  const node = nodes.find((item) => item.id === nodeId)
  if (!node) return undefined
  if (node.type === 'asset') {
    return node.data.mediaType === kind && node.data.value?.url
      ? { kind, url: node.data.value.url } : undefined
  }
  if (['stale', 'failed', 'canceled', 'queued', 'running'].includes(node.data.runtime.status)) return undefined
  const upstream = edges.find((edge) => edge.target === nodeId && edge.targetHandle === 'in:media')
  if (upstream) {
    const source = nodes.find((item) => item.id === upstream.source)
    if (source && !source.type?.endsWith('-view')) {
      if (['stale', 'failed', 'canceled', 'queued', 'running'].includes(source.data.runtime.status)) return undefined
      const outputs = source.data.outputs?.filter((output) => output.kind === kind && output.url) ?? []
      return outputs[Math.min(node.data.selectedOutput ?? 0, Math.max(0, outputs.length - 1))]
    }
    return mediaForNode(upstream.source, kind, nodes, edges, visited)
  }
  const outputs = node.data.outputs?.filter((output) => output.kind === kind && output.url) ?? []
  return outputs[Math.min(node.data.selectedOutput ?? 0, Math.max(0, outputs.length - 1))]
}

export function buildExportRequest(
  production: Production, projectId: string, nodes: CanvasNode[], edges: CanvasEdge[],
): ExportRequest {
  if (!projectId) throw new Error('请先保存作品，再导出成片')
  if (!production.timeline.length) throw new Error('请先创建分镜并同步时间线')
  const shots = new Map(production.shots.map((shot) => [shot.id, shot]))
  const seen = new Set<string>()
  const clips = [...production.timeline].sort((a, b) => a.order - b.order).map((clip) => {
    const shot = shots.get(clip.shot_id)
    if (!shot || seen.has(clip.shot_id)) throw new Error('时间线包含重复或已删除的镜头，请同步分镜顺序')
    seen.add(clip.shot_id)
    if (!Number.isFinite(clip.trim_start) || !Number.isFinite(clip.trim_end)
      || clip.trim_start < 0 || clip.trim_end <= clip.trim_start) {
      throw new Error(`「${shot.title}」裁切范围无效：结束时间应大于开始时间`)
    }
    const output = mediaForShot(shot, nodes, edges)
    if (!output?.url) throw new Error(`「${shot.title}」还没有可用视频，请运行节点或从素材库应用视频`)
    return { url: output.url, trim_start: clip.trim_start, trim_end: clip.trim_end, subtitle: clip.subtitle }
  })
  return {
    project_id: projectId, clips, audio_urls: [],
    aspect_ratio: production.aspect_ratio, resolution: '1080p', fps: 24,
  }
}

export function mediaForShot(shot: Production['shots'][number] | undefined, nodes: CanvasNode[], edges: CanvasEdge[]): NodeOutput | undefined {
  if (!shot) return undefined
  if (shot.node_ids?.compose) return mediaForNode(shot.node_ids.compose, 'video', nodes, edges)
  return mediaForNode(shot.node_ids?.video_view, 'video', nodes, edges)
    ?? mediaForNode(shot.node_ids?.video, 'video', nodes, edges)
}

export function createProduction(kind: Production['kind']): Production {
  return {
    kind, brief: '', style: kind === 'comic' ? '电影感国漫' : '自然质感商品广告',
    aspect_ratio: kind === 'comic' ? '9:16' : '16:9', duration_seconds: 20,
    script: '', characters: [], scenes: [], episodes: [], shots: [], timeline: [],
  }
}

function shotInputs(production: Production, shot: Production['shots'][number]): string {
  return JSON.stringify({
    kind: production.kind, brief: production.brief, style: production.style,
    aspect_ratio: production.aspect_ratio, script: production.script,
    title: shot.title, description: shot.description, image_prompt: shot.image_prompt,
    video_prompt: shot.video_prompt, dialogue: shot.dialogue, duration_seconds: shot.duration_seconds,
    character_ids: shot.character_ids, scene_id: shot.scene_id, episode_id: shot.episode_id,
    characters: shot.character_ids.map((id) => production.characters.find((item) => item.id === id)),
    scene: production.scenes.find((item) => item.id === shot.scene_id),
    episode: production.episodes.find((item) => item.id === shot.episode_id),
  })
}

/** A content edit detaches old node associations; old canvas graphs stay available as previous versions. */
export function reconcileProductionEdit(previous: Production, next: Production): Production {
  const previousShots = new Map(previous.shots.map((shot) => [shot.id, shot]))
  let invalidated = false
  const shots = next.shots.map((shot) => {
    const before = previousShots.get(shot.id)
    if (!before?.node_ids || shotInputs(previous, before) === shotInputs(next, shot)) return shot
    invalidated = true
    return { ...shot, node_ids: undefined }
  })
  return invalidated ? { ...next, shots } : next
}

export function reconcileNodeJobs(node: CanvasNode, jobs: StudioJob[]): Partial<CanvasNode['data']> | null {
  if (node.data.runtime.status === 'stale') return null
  const ids = node.data.runtime.jobIds?.length ? node.data.runtime.jobIds
    : node.data.runtime.jobId ? [node.data.runtime.jobId] : []
  if (!ids.length) return null
  const byId = new Map(jobs.filter((job) => job.node_id === node.id).map((job) => [job.id, job]))
  const currentJobs = ids.map((id) => byId.get(id))
  if (currentJobs.some((job) => !job || job.status === 'queued' || job.status === 'running')) return null
  const completeJobs = currentJobs as StudioJob[]
  const outputs = completeJobs.flatMap((job) => job.status === 'succeeded' ? job.outputs ?? [] : [])
  const errors = completeJobs.filter((job) => job.status !== 'succeeded').map((job) => job.error || '任务未成功')
  const status = outputs.length ? 'succeeded'
    : completeJobs.every((job) => job.status === 'canceled') ? 'canceled' : 'failed'
  const patch = {
    runtime: { ...node.data.runtime, status, error: errors.length ? errors.join('；') : undefined },
    outputs,
  } as Partial<CanvasNode['data']>
  return JSON.stringify(node.data.runtime) === JSON.stringify(patch.runtime)
    && JSON.stringify(node.data.outputs) === JSON.stringify(outputs) ? null : patch
}

export function canRetryNodeJob(node: CanvasNode | undefined, job: StudioJob): boolean {
  if (!node) return true
  const ids = node.data.runtime.jobIds ?? (node.data.runtime.jobId ? [node.data.runtime.jobId] : [])
  return ['failed', 'canceled'].includes(node.data.runtime.status) && ids.includes(job.id)
}
