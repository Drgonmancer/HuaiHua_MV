import type { MediaKind, NodeKind, CanvasNode, CanvasEdge } from '../types'

// 端口规则单一事实源。后端 agent/plan 校验持有一份 Python 镜像
// （app.py 的 _CANVAS_PORT_RULES），改这里必须同步改那边。
export type PortRule = { accepts: MediaKind[]; max: number }

export type NodeRule = {
  produces: MediaKind | 'dynamic'
  inputs: Record<string, PortRule>
}

export const PORT_RULES: Record<NodeKind, NodeRule> = {
  prompt: {
    produces: 'text',
    inputs: { 'in:context': { accepts: ['text'], max: 3 } },
  },
  asset: { produces: 'dynamic', inputs: {} },
  'image-gen': {
    produces: 'image',
    inputs: {
      'in:prompt': { accepts: ['text'], max: 4 },
      'in:ref': { accepts: ['image'], max: 6 },
    },
  },
  'image-view': {
    produces: 'image',
    inputs: { 'in:media': { accepts: ['image'], max: 1 } },
  },
  'video-gen': {
    produces: 'video',
    inputs: {
      'in:prompt': { accepts: ['text'], max: 2 },
      'in:first': { accepts: ['image'], max: 1 },
      'in:last': { accepts: ['image'], max: 1 },
    },
  },
  'video-view': {
    produces: 'video',
    inputs: { 'in:media': { accepts: ['video'], max: 1 } },
  },
  'audio-gen': {
    produces: 'audio',
    inputs: { 'in:text': { accepts: ['text'], max: 1 } },
  },
  'audio-view': {
    produces: 'audio',
    inputs: { 'in:media': { accepts: ['audio'], max: 1 } },
  },
  compose: {
    produces: 'video',
    inputs: {
      'in:video': { accepts: ['video'], max: 1 },
      'in:audio': { accepts: ['audio'], max: 2 },
    },
  },
  group: { produces: 'dynamic', inputs: {} },
}

export function nodeProduces(node: CanvasNode): MediaKind | null {
  const rule = PORT_RULES[node.type as NodeKind]
  if (!rule) return null
  if (rule.produces === 'dynamic') {
    if (node.type === 'asset') return node.data.mediaType ?? 'image'
    return null
  }
  return rule.produces
}

export type ConnectionCheck =
  | { ok: true; mediaType: MediaKind }
  | { ok: false; reason: string }

export function checkConnection(
  source: CanvasNode | undefined,
  target: CanvasNode | undefined,
  targetHandle: string | null | undefined,
  edges: CanvasEdge[],
): ConnectionCheck {
  if (!source || !target) return { ok: false, reason: '节点不存在' }
  if (source.id === target.id) return { ok: false, reason: '不能连接自身' }

  const produced = nodeProduces(source)
  if (!produced) return { ok: false, reason: '该节点没有可用输出' }

  const targetRule = PORT_RULES[target.type as NodeKind]
  if (!targetRule) return { ok: false, reason: '未知节点类型' }

  const handle = targetHandle ?? ''
  const portRule = targetRule.inputs[handle]
  if (!portRule) return { ok: false, reason: '该节点没有此输入端口' }

  if (!portRule.accepts.includes(produced)) {
    return {
      ok: false,
      reason: `此端口只接受 ${portRule.accepts.join('/')}，上游输出是 ${produced}`,
    }
  }

  const existing = edges.filter(
    (e) => e.target === target.id && e.targetHandle === handle,
  )
  if (existing.some((e) => e.source === source.id)) {
    return { ok: false, reason: '两个节点在此端口已有连线' }
  }
  if (existing.length >= portRule.max) {
    return { ok: false, reason: `此端口最多接受 ${portRule.max} 个输入` }
  }

  return { ok: true, mediaType: produced }
}

/** 从 target 沿出边 DFS，若可达 source 则新连线会成环。 */
export function wouldCreateCycle(
  sourceId: string,
  targetId: string,
  edges: CanvasEdge[],
): boolean {
  const adjacency = new Map<string, string[]>()
  for (const e of edges) {
    const list = adjacency.get(e.source) ?? []
    list.push(e.target)
    adjacency.set(e.source, list)
  }
  const stack = [targetId]
  const seen = new Set<string>()
  while (stack.length) {
    const current = stack.pop()!
    if (current === sourceId) return true
    if (seen.has(current)) continue
    seen.add(current)
    for (const next of adjacency.get(current) ?? []) stack.push(next)
  }
  return false
}
