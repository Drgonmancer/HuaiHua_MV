/**
 * 节点内容指纹：稳定序列化（键排序）后哈希。
 * 指纹相同 → 复用上次成功结果，不重新提交供应商请求。
 * 指纹不同（提示词/参数/上游产物变化）→ 正常重新生成。
 */

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return '[' + value.map(stableStringify).join(',') + ']'
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  return '{' + entries.map(([key, item]) => JSON.stringify(key) + ':' + stableStringify(item)).join(',') + '}'
}

/** 简易非加密哈希（FNV-1a 32 位 ×2），足够检测输入变化即可 */
function hash(input: string): string {
  let a = 0x811c9dc5
  let b = 0x01000193
  for (let index = 0; index < input.length; index += 1) {
    const code = input.charCodeAt(index)
    a = Math.imul(a ^ code, 16777619) >>> 0
    b = Math.imul(b + code, 2246822519) >>> 0
  }
  return (a.toString(16).padStart(8, '0') + b.toString(16).padStart(8, '0'))
}

export function nodeFingerprint(
  type: string | undefined,
  params: unknown,
  inputs: Record<string, string[]> | undefined,
): string {
  return hash(stableStringify({ type, params, inputs }))
}
