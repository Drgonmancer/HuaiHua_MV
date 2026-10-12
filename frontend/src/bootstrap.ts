export type CanvasBootstrap = {
  user_id: string | null
  username: string
  credits: number
  project_id: string | null
  dev?: boolean
}

declare global {
  interface Window {
    __CANVAS_BOOTSTRAP__?: CanvasBootstrap
  }
}

export function getBootstrap(): CanvasBootstrap {
  return (
    window.__CANVAS_BOOTSTRAP__ ?? {
      user_id: null,
      username: '用户',
      credits: 0,
      project_id: null,
    }
  )
}
