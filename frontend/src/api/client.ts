export class ApiError extends Error {
  status: number
  payload: Record<string, unknown>

  constructor(status: number, payload: Record<string, unknown>) {
    super(String(payload.error ?? `请求失败（${status}）`))
    this.status = status
    this.payload = payload
  }
}

export async function api<T = Record<string, unknown>>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options,
  })
  let payload: Record<string, unknown> = {}
  try {
    payload = await response.json()
  } catch {
    // 非 JSON 响应（如网关 502 页面）
  }
  if (!response.ok || payload.success === false) {
    throw new ApiError(response.status, payload)
  }
  return payload as T
}
