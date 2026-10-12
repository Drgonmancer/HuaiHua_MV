import { api } from './client'

export type VideoModel = {
  public_model_id: string
  name: string
  supported_modes?: string[]
  aspect_ratios?: string[]
  verified_durations?: number[]
  credits_cost?: number
  credits_per_second?: number
  enabled?: boolean
  resolution?: string
  supported_resolutions?: string[]
  supports_end_image?: boolean
}

let cache: VideoModel[] | null = null
let inflight: Promise<VideoModel[]> | null = null

export function getVideoModels(): Promise<VideoModel[]> {
  if (cache) return Promise.resolve(cache)
  if (!inflight) {
    inflight = api<{ models: VideoModel[] }>('/api/video/models')
      .then((result) => {
        cache = (result.models ?? []).filter(
          (m) => m.enabled !== false,
        )
        return cache
      })
      .catch(() => {
        inflight = null
        return []
      })
  }
  return inflight
}

export function estimateVideoCredits(model: VideoModel | undefined, durationSeconds: number): number {
  if (!model) return 0
  const perSecond = Number(model.credits_per_second ?? 0)
  if (perSecond > 0 && durationSeconds > 0) return perSecond * durationSeconds
  return Number(model.credits_cost ?? 0)
}
