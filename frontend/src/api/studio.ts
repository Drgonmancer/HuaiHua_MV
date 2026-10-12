import { api, ApiError } from './client'
import type {
  ExportRequest, Production, ProductionKind, StoryboardResult, StudioAsset, StudioJob,
  StudioProject, StudioSettings, StudioTemplate,
} from '../studioTypes'

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })
const encode = encodeURIComponent

export const studioApi = {
  projects: () => api<{ projects: StudioProject[] }>('/api/studio/projects'),
  project: (id: string) => api<{ project: StudioProject }>(`/api/studio/projects/${encode(id)}`),
  createProject: (input: { title: string; kind: ProductionKind; brief?: string; style?: string; aspect_ratio?: string }) =>
    api<{ project: StudioProject }>('/api/studio/projects', json(input)),
  deleteProject: (id: string) => api(`/api/studio/projects/${encode(id)}`, { method: 'DELETE' }),
  templates: () => api<{ templates: StudioTemplate[] }>('/api/studio/templates'),
  storyboard: (production: Production, source: 'template' | 'ai', project_id?: string, episode_id?: string) =>
    api<StoryboardResult>('/api/studio/storyboard', json({ production, source, project_id, episode_id })),
  assets: (category = '', query = '', signal?: AbortSignal) => {
    const params = new URLSearchParams({ category, q: query })
    return api<{ assets: StudioAsset[] }>(`/api/studio/assets?${params}`, { signal })
  },
  async uploadAsset(file: File, name: string, category: string): Promise<{ asset: StudioAsset }> {
    const body = new FormData()
    body.append('file', file)
    body.append('name', name || file.name)
    body.append('category', category)
    const response = await fetch('/api/studio/assets', { method: 'POST', credentials: 'same-origin', body })
    let payload: Record<string, unknown> = {}
    try { payload = await response.json() } catch { /* error reported below */ }
    if (!response.ok || payload.success === false) throw new ApiError(response.status, payload)
    return payload as { asset: StudioAsset }
  },
  deleteAsset: (id: string) => api(`/api/studio/assets/${encode(id)}`, { method: 'DELETE' }),
  jobs: (projectId?: string | null, signal?: AbortSignal) =>
    api<{ jobs: StudioJob[] }>(`/api/studio/jobs${projectId ? `?project_id=${encode(projectId)}` : ''}`, { signal }),
  retryJob: (id: string) => api<{ job: StudioJob }>(`/api/studio/jobs/${encode(id)}/retry`, json({})),
  cancelJob: (id: string) => api<{ job: StudioJob }>(`/api/studio/jobs/${encode(id)}/cancel`, json({})),
  settings: () => api<StudioSettings>('/api/studio/settings'),
  audioMetadata: () => api<{
    configured: boolean
    models: Array<string | { id?: string; name?: string; model?: string }>
    voices: Array<string | { id?: string; name?: string; voice_id?: string }>
  }>('/api/canvas/audio-meta'),
  export: (request: ExportRequest) => api<{ job: StudioJob }>('/api/studio/export', json(request)),
}

export function studioError(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return `服务暂不可用：${error.message}。请在「配置」查看供应商与 FFmpeg 状态。`
  }
  if (error instanceof Error) return error.message
  return '操作失败，请检查本地服务后重试'
}
