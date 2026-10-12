import { api } from './client'

export type Voice = { id: string; label: string }
export type TtsModel = { id: string; label: string; credits_per_100_chars: number }

export type AudioMeta = {
  configured: boolean
  models: TtsModel[]
  voices: Voice[]
}

const FALLBACK: AudioMeta = {
  configured: false,
  models: [],
  voices: [],
}

let cache: AudioMeta | null = null
let inflight: Promise<AudioMeta> | null = null

export function getAudioMeta(): Promise<AudioMeta> {
  if (cache) return Promise.resolve(cache)
  if (!inflight) {
    inflight = api<{
      configured?: boolean
      models?: TtsModel[]
      voices?: Voice[]
    }>('/api/canvas/audio-meta')
      .then((result) => {
        cache = {
          configured: Boolean(result.configured),
          models: result.models?.length ? result.models : FALLBACK.models,
          voices: result.voices?.length ? result.voices : FALLBACK.voices,
        }
        return cache
      })
      .catch(() => {
        inflight = null
        return FALLBACK
      })
  }
  return inflight
}

/** 按字符数与档位单价预估积分（ceil(字符/100)×单价）。 */
export function estimateTtsCredits(model: TtsModel | undefined, charCount: number): number {
  if (!model || charCount <= 0) return 0
  return Math.ceil(charCount / 100) * model.credits_per_100_chars
}
