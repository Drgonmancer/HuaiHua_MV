import type { CanvasEdge } from '../types'

/** 生成类节点（会产生异步任务、参与 runToNode 执行链） */
export const GEN_NODE_TYPES = new Set(['image-gen', 'video-gen', 'audio-gen', 'compose'])

/** 返回 nodeId 的全部祖先 + 自身，按拓扑序（上游在前）。 */
export function topoAncestors(nodeId: string, edges: CanvasEdge[]): string[] {
  const incoming = new Map<string, string[]>()
  for (const edge of edges) {
    const list = incoming.get(edge.target) ?? []
    list.push(edge.source)
    incoming.set(edge.target, list)
  }

  // 反向 BFS 收集祖先集合
  const relevant = new Set<string>()
  const stack = [nodeId]
  while (stack.length) {
    const current = stack.pop()!
    if (relevant.has(current)) continue
    relevant.add(current)
    for (const parent of incoming.get(current) ?? []) stack.push(parent)
  }

  return topoSort([...relevant], edges)
}

/** 对给定节点集合做 Kahn 拓扑排序（只考虑集合内的边）。含环时抛错。 */
export function topoSort(nodeIds: string[], edges: CanvasEdge[]): string[] {
  const inSet = new Set(nodeIds)
  const indegree = new Map<string, number>()
  const adjacency = new Map<string, string[]>()
  for (const id of nodeIds) indegree.set(id, 0)
  for (const edge of edges) {
    if (!inSet.has(edge.source) || !inSet.has(edge.target)) continue
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1)
    const list = adjacency.get(edge.source) ?? []
    list.push(edge.target)
    adjacency.set(edge.source, list)
  }

  const queue = nodeIds.filter((id) => (indegree.get(id) ?? 0) === 0)
  const order: string[] = []
  while (queue.length) {
    const current = queue.shift()!
    order.push(current)
    for (const next of adjacency.get(current) ?? []) {
      const remaining = (indegree.get(next) ?? 0) - 1
      indegree.set(next, remaining)
      if (remaining === 0) queue.push(next)
    }
  }
  if (order.length !== nodeIds.length) {
    throw new Error('工作流存在循环依赖')
  }
  return order
}

/** nodeId 的全部后代（不含自身），用于 stale 传播。 */
export function descendants(nodeId: string, edges: CanvasEdge[]): string[] {
  const adjacency = new Map<string, string[]>()
  for (const edge of edges) {
    const list = adjacency.get(edge.source) ?? []
    list.push(edge.target)
    adjacency.set(edge.source, list)
  }
  const result = new Set<string>()
  const stack = [...(adjacency.get(nodeId) ?? [])]
  while (stack.length) {
    const current = stack.pop()!
    if (result.has(current)) continue
    result.add(current)
    stack.push(...(adjacency.get(current) ?? []))
  }
  return [...result]
}
