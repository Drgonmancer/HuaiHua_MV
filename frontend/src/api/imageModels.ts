import { api } from './client'

export type ImageModel = { id: string; label: string; configured: boolean }

let request: Promise<ImageModel[]> | null = null

export function getImageModels(): Promise<ImageModel[]> {
  if (!request) {
    request = api<{providers: {image: {configured: boolean; model: string; models?: string[]}}}>('/api/studio/settings')
      .then(({providers}) => providers.image.configured
        ? (providers.image.models?.length ? providers.image.models : [providers.image.model])
          .map((model) => ({id: model, label: model, configured: true}))
        : [])
      .catch(() => { request = null; return [] })
  }
  return request
}
