import { create } from 'zustand'
import {
  applyNodeChanges,
  applyEdgeChanges,
  type NodeChange,
  type EdgeChange,
  type Connection,
} from '@xyflow/react'
import type {
  CanvasNode,
  CanvasEdge,
  CanvasNodeData,
  NodeKind,
  Viewport,
} from '../types'
import { checkConnection, wouldCreateCycle } from '../nodes/portRules'
import { descendants } from '../engine/graph'

let idCounter = 0
export function newId(prefix: string): string {
  idCounter += 1
  return `${prefix}-${Date.now().toString(36)}-${idCounter.toString(36)}`
}

export type Toast = { id: string; message: string; tone: 'info' | 'error' | 'success' }

const NODE_DEFAULTS: Record<NodeKind, () => Partial<CanvasNodeData> & { title: string }> = {
  prompt: () => ({ title: '提示词', value: { text: '' } }),
  asset: () => ({ title: '参考图', mediaType: 'image' }),
  'image-gen': () => ({
    title: '文生图',
    params: { model: 'gpt_image_2', ratio: '1:1', size: '2K', count: 1 },
  }),
  'image-view': () => ({ title: '图片视图' }),
  'video-gen': () => ({
    title: '视频生成',
    // 画布视频仅开放 Seedance 系列，默认普通版（25 积分/秒）。
    // resolution 留空 → 由模型目录的默认档驱动（Seedance t2v 不接受 1080p）
    params: { model: 'seedance_2_0', ratio: '16:9', durationSeconds: 5 },
  }),
  'video-view': () => ({ title: '视频视图' }),
  'audio-gen': () => ({
    title: '音频生成',
    // 默认普通版 tts-1（5 积分/百字），Alloy 中性音
    params: { mode: 'tts', model: 'tts-1', voiceId: 'alloy', speed: 1 },
  }),
  'audio-view': () => ({ title: '音频视图' }),
  compose: () => ({ title: '合成' }),
  group: () => ({ title: '分组' }),
}

export function createNode(
  type: NodeKind,
  position: { x: number; y: number },
  overrides?: Partial<CanvasNodeData>,
): CanvasNode {
  const defaults = NODE_DEFAULTS[type]()
  const node: CanvasNode = {
    id: newId(type),
    type,
    position,
    data: {
      params: {},
      runtime: { status: 'idle' },
      ...defaults,
      ...overrides,
    } as CanvasNodeData,
  }
  if (type === 'group') {
    node.width = 480
    node.height = 360
    // 分组框永远垫底，不遮挡成员节点
    node.zIndex = -1
  }
  return node
}

type FlowState = {
  nodes: CanvasNode[]
  edges: CanvasEdge[]
  viewport: Viewport
  revision: number
  toasts: Toast[]

  onNodesChange: (changes: NodeChange<CanvasNode>[]) => void
  onEdgesChange: (changes: EdgeChange<CanvasEdge>[]) => void
  onConnect: (connection: Connection) => void
  isValidConnection: (connection: Connection | CanvasEdge) => boolean

  setGraph: (nodes: CanvasNode[], edges: CanvasEdge[], viewport?: Viewport) => void
  setViewport: (viewport: Viewport) => void
  addNode: (node: CanvasNode) => void
  addFragment: (nodes: CanvasNode[], edges: CanvasEdge[]) => void
  updateNodeData: (nodeId: string, patch: Partial<CanvasNodeData>) => void
  updateNodeParams: (nodeId: string, patch: CanvasNodeData['params']) => void
  /** 用户内容编辑（提示词文本/素材替换）：更新 value 并使自身与下游成果过期 */
  updateNodeValue: (nodeId: string, value: CanvasNodeData['value']) => void
  duplicateSelected: () => void
  assignParent: (nodeId: string, groupId: string | null) => void
  cycleEdgeOrder: (edgeId: string) => void

  pushToast: (message: string, tone?: Toast['tone']) => void
  dismissToast: (id: string) => void
}

/** 把 fromId 的后代（可含自身）中"已成功"的节点打成 stale */
function markStale(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  fromId: string,
  includeSelf: boolean,
): CanvasNode[] {
  const targets = new Set(descendants(fromId, edges))
  if (includeSelf) targets.add(fromId)
  if (!targets.size) return nodes
  let changed = false
  const next = nodes.map((node) => {
    if (!targets.has(node.id)) return node
    if (!['succeeded', 'queued', 'running'].includes(node.data.runtime.status)) return node
    changed = true
    return {
      ...node,
      data: { ...node.data, runtime: { ...node.data.runtime, status: 'stale' as const } },
    }
  })
  return changed ? next : nodes
}

/** 同 target+handle 分组内按现有 order 重排为 1..n（新边追加在末尾）。 */
function renumberEdges(edges: CanvasEdge[]): CanvasEdge[] {
  const groups = new Map<string, CanvasEdge[]>()
  for (const edge of edges) {
    const key = `${edge.target}::${edge.targetHandle ?? ''}`
    const list = groups.get(key) ?? []
    list.push(edge)
    groups.set(key, list)
  }
  const orderById = new Map<string, number>()
  for (const list of groups.values()) {
    list.sort((a, b) => (a.data?.order ?? 0) - (b.data?.order ?? 0))
    list.forEach((edge, index) => orderById.set(edge.id, index + 1))
  }
  return edges.map((edge) => {
    const order = orderById.get(edge.id) ?? 1
    if (edge.data?.order === order) return edge
    return { ...edge, data: { ...(edge.data ?? { mediaType: 'text' as const }), order } }
  })
}

export const useFlowStore = create<FlowState>((set, get) => ({
  nodes: [],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
  revision: 0,
  toasts: [],

  onNodesChange: (changes) => {
    set((state) => {
      let nodes = applyNodeChanges(changes, state.nodes)
      const removedIds = new Set(
        changes.filter((c) => c.type === 'remove').map((c) => c.id),
      )
      if (removedIds.size) {
        // 删除分组时释放成员（转回绝对坐标由 React Flow 的 parent 删除逻辑处理不了，这里手动释放）
        nodes = nodes.map((node) =>
          node.parentId && removedIds.has(node.parentId)
            ? { ...node, parentId: undefined, extent: undefined }
            : node,
        )
      }
      const structural = changes.some((c) => c.type !== 'select' && c.type !== 'dimensions')
      return {
        nodes,
        edges: removedIds.size
          ? renumberEdges(state.edges.filter((e) => !removedIds.has(e.source) && !removedIds.has(e.target)))
          : state.edges,
        revision: structural ? state.revision + 1 : state.revision,
      }
    })
  },

  onEdgesChange: (changes) => {
    set((state) => {
      const edges = applyEdgeChanges(changes, state.edges)
      const structural = changes.some((c) => c.type !== 'select')
      let nodes = state.nodes
      if (structural) {
        // 删边 → 原 target 及其下游成果过期（用删除前的边表找 target）
        for (const change of changes) {
          if (change.type !== 'remove') continue
          const removed = state.edges.find((e) => e.id === change.id)
          if (removed) nodes = markStale(nodes, state.edges, removed.target, true)
        }
      }
      return {
        nodes,
        edges: structural ? renumberEdges(edges) : edges,
        revision: structural ? state.revision + 1 : state.revision,
      }
    })
  },

  isValidConnection: (connection) => {
    const { nodes, edges } = get()
    const source = nodes.find((n) => n.id === connection.source)
    const target = nodes.find((n) => n.id === connection.target)
    const check = checkConnection(source, target, connection.targetHandle, edges)
    if (!check.ok) return false
    return !wouldCreateCycle(connection.source!, connection.target!, edges)
  },

  onConnect: (connection) => {
    const { nodes, edges, pushToast } = get()
    const source = nodes.find((n) => n.id === connection.source)
    const target = nodes.find((n) => n.id === connection.target)
    const check = checkConnection(source, target, connection.targetHandle, edges)
    if (!check.ok) {
      pushToast(check.reason, 'error')
      return
    }
    if (wouldCreateCycle(connection.source, connection.target, edges)) {
      pushToast('不允许成环连接', 'error')
      return
    }
    const siblingCount = edges.filter(
      (e) => e.target === connection.target && e.targetHandle === connection.targetHandle,
    ).length
    const edge: CanvasEdge = {
      id: newId('edge'),
      source: connection.source,
      target: connection.target,
      sourceHandle: connection.sourceHandle ?? 'out',
      targetHandle: connection.targetHandle ?? undefined,
      type: 'ordered',
      data: { order: siblingCount + 1, mediaType: check.mediaType },
    }
    set((state) => ({
      edges: renumberEdges([...state.edges, edge]),
      // 输入拓扑变化 → 下游已成功的成果过期
      nodes: markStale(state.nodes, [...state.edges, edge], connection.target, true),
      revision: state.revision + 1,
    }))
  },

  setGraph: (nodes, edges, viewport) => {
    set((state) => ({
      nodes,
      edges,
      viewport: viewport ?? state.viewport,
      revision: state.revision + 1,
    }))
  },

  setViewport: (viewport) => set({ viewport }),

  addNode: (node) => {
    set((state) => ({ nodes: [...state.nodes, node], revision: state.revision + 1 }))
  },

  addFragment: (nodes, edges) => {
    set((state) => ({
      nodes: [...state.nodes, ...nodes],
      edges: renumberEdges([...state.edges, ...edges]),
      revision: state.revision + 1,
    }))
  },

  updateNodeData: (nodeId, patch) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node,
      ),
      revision: state.revision + 1,
    }))
  },

  updateNodeParams: (nodeId, patch) => {
    set((state) => {
      let nodes = state.nodes.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, params: { ...node.data.params, ...patch } } }
          : node,
      )
      nodes = markStale(nodes, state.edges, nodeId, true)
      return { nodes, revision: state.revision + 1 }
    })
  },

  updateNodeValue: (nodeId, value) => {
    set((state) => {
      let nodes = state.nodes.map((node) =>
        node.id === nodeId ? { ...node, data: { ...node.data, value } } : node,
      )
      nodes = markStale(nodes, state.edges, nodeId, true)
      return { nodes, revision: state.revision + 1 }
    })
  },

  duplicateSelected: () => {
    const { nodes } = get()
    const selected = nodes.filter((n) => n.selected && n.type !== 'group')
    if (!selected.length) return
    const copies = selected.map((node) => ({
      ...createNode(node.type as NodeKind, {
        x: node.position.x + 40,
        y: node.position.y + 40,
      }),
      parentId: node.parentId,
      extent: node.extent,
      data: {
        ...structuredClone(node.data),
        runtime: { status: 'idle' as const },
      },
    }))
    set((state) => ({
      nodes: [
        ...state.nodes.map((n) => ({ ...n, selected: false })),
        ...copies.map((n) => ({ ...n, selected: true })),
      ],
      revision: state.revision + 1,
    }))
  },

  assignParent: (nodeId, groupId) => {
    set((state) => {
      const node = state.nodes.find((n) => n.id === nodeId)
      if (!node || node.type === 'group') return state
      if ((node.parentId ?? null) === groupId) return state
      const group = groupId ? state.nodes.find((n) => n.id === groupId) : null

      // parentId 变化时换算相对/绝对坐标，保持视觉位置不变
      const abs = { ...node.position }
      if (node.parentId) {
        const oldGroup = state.nodes.find((n) => n.id === node.parentId)
        if (oldGroup) {
          abs.x += oldGroup.position.x
          abs.y += oldGroup.position.y
        }
      }
      const position = group
        ? { x: abs.x - group.position.x, y: abs.y - group.position.y }
        : abs

      // React Flow 要求 parent 节点排在 child 之前
      const others = state.nodes.filter((n) => n.id !== nodeId)
      const updated: CanvasNode = {
        ...node,
        position,
        parentId: groupId ?? undefined,
        extent: undefined,
      }
      const groupIndex = groupId ? others.findIndex((n) => n.id === groupId) : -1
      const nodes = [...others]
      nodes.splice(groupIndex + 1, 0, updated)
      return { nodes, revision: state.revision + 1 }
    })
  },

  cycleEdgeOrder: (edgeId) => {
    set((state) => {
      const edge = state.edges.find((e) => e.id === edgeId)
      if (!edge) return state
      const siblings = state.edges
        .filter((e) => e.target === edge.target && e.targetHandle === edge.targetHandle)
        .sort((a, b) => (a.data?.order ?? 0) - (b.data?.order ?? 0))
      if (siblings.length < 2) return state
      const index = siblings.findIndex((e) => e.id === edgeId)
      // 每次点击往前挪一位；已是第 ① 则轮转到末尾
      const swapWith = index === 0 ? siblings.length - 1 : index - 1
      const orders = siblings.map((e) => e.data?.order ?? 1)
      const swapped = new Map<string, number>()
      swapped.set(siblings[index].id, orders[swapWith])
      swapped.set(siblings[swapWith].id, orders[index])
      return {
        edges: state.edges.map((e) =>
          swapped.has(e.id)
            ? { ...e, data: { ...(e.data ?? { mediaType: 'text' as const }), order: swapped.get(e.id)! } }
            : e,
        ),
        revision: state.revision + 1,
      }
    })
  },

  pushToast: (message, tone = 'info') => {
    const toast: Toast = { id: newId('toast'), message, tone }
    set((state) => ({ toasts: [...state.toasts.slice(-3), toast] }))
    setTimeout(() => get().dismissToast(toast.id), 3200)
  },

  dismissToast: (id) => {
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }))
  },
}))
