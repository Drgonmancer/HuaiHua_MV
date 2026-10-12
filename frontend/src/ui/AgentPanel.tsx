import { useState } from 'react'
import { api, ApiError } from '../api/client'
import { useFlowStore, createNode, newId } from '../store/flowStore'
import { runAll } from '../engine/executor'
import { TracePanel } from './TracePanel'
import type { CanvasNode, CanvasEdge, NodeKind, MediaKind } from '../types'
import { useProductionStore } from '../store/productionStore'
import { useProjectStore } from '../store/projectStore'

type PlanNode = {
  id: string
  type: NodeKind
  title: string
  text?: string
  params?: Record<string, unknown>
  x: number
  y: number
}

type PlanEdge = {
  source: string
  target: string
  targetHandle: string
  data: { order: number; mediaType: MediaKind }
}

type Plan = { title: string; nodes: PlanNode[]; edges: PlanEdge[] }

/** Agent 搭图入口：一句话 → 角色型节点图落到画布（追加在现有内容右侧） */
export function AgentPanel() {
  const [brief, setBrief] = useState('')
  const [loading, setLoading] = useState(false)
  const [proMode, setProMode] = useState(false)
  const [showTrace, setShowTrace] = useState(false)
  const [runningAll, setRunningAll] = useState(false)
  const addFragment = useFlowStore((s) => s.addFragment)
  const pushToast = useFlowStore((s) => s.pushToast)

  const handleRunAll = async () => {
    if (runningAll) return
    setRunningAll(true)
    setShowTrace(true)
    try {
      const result = await runAll()
      pushToast(
        result.total
          ? `执行完成：${result.succeeded}/${result.total} 个节点成功`
          : '没有待执行的生成节点',
        result.succeeded === result.total ? 'success' : 'error',
      )
    } finally {
      setRunningAll(false)
    }
  }

  const handleSubmit = async () => {
    const trimmed = brief.trim()
    if (trimmed.length < 4) {
      pushToast('请描述至少 4 个字的工作流需求', 'error')
      return
    }
    setLoading(true)
    try {
      const result = await api<{ plan: Plan; credits_cost: number }>(
        '/api/canvas/agent/plan',
        { method: 'POST', body: JSON.stringify({ brief: trimmed, production: useProductionStore.getState().production, project_id: useProjectStore.getState().projectId }) },
      )
      const plan = result.plan

      // 追加到现有内容右下方，避免覆盖
      const existing = useFlowStore.getState().nodes
      const offsetX = existing.length
        ? Math.max(...existing.map((n) => n.position.x)) + 480
        : 120
      const offsetY = 120

      const idMap = new Map<string, string>()
      const nodes: CanvasNode[] = plan.nodes.map((planNode) => {
        const node = createNode(planNode.type, {
          x: planNode.x + offsetX,
          y: planNode.y + offsetY,
        })
        idMap.set(planNode.id, node.id)
        node.data.title = planNode.title || node.data.title
        if (planNode.type === 'prompt' && planNode.text) {
          node.data.value = { text: planNode.text }
        }
        if (planNode.params) {
          node.data.params = { ...node.data.params, ...planNode.params }
        }
        return node
      })

      const edges: CanvasEdge[] = plan.edges
        .filter((e) => idMap.has(e.source) && idMap.has(e.target))
        .map((planEdge) => ({
          id: newId('edge'),
          source: idMap.get(planEdge.source)!,
          target: idMap.get(planEdge.target)!,
          sourceHandle: 'out',
          targetHandle: planEdge.targetHandle,
          type: 'ordered',
          data: planEdge.data,
        }))

      addFragment(nodes, edges)
      setBrief('')
      pushToast(
        `已搭好「${plan.title}」：${nodes.length} 节点 ${edges.length} 连线`,
        'success',
      )
      if (proMode) {
        // Pro Mode：落图后立刻批量执行并展示轨迹
        void handleRunAll()
      }
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : '搭图失败，请稍后重试'
      pushToast(message, 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      {showTrace && <TracePanel onClose={() => setShowTrace(false)} />}
      <div className="canvas-agent-panel" data-testid="agent-panel">
        <label className="canvas-agent-promode" title="搭图后自动批量执行并展示轨迹">
          <input
            type="checkbox"
            checked={proMode}
            onChange={(e) => setProMode(e.target.checked)}
          />
          Pro
        </label>
        <input
          value={brief}
          placeholder="描述商品宣传片或漫剧：题材、角色、镜头、画风与时长…"
          aria-label="AI 工作流需求"
          disabled={loading}
          onChange={(e) => setBrief(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void handleSubmit()
          }}
        />
        <button disabled={loading} onClick={() => void handleSubmit()}>
          {loading ? '搭建中…' : '搭图'}
        </button>
        <button
          className="secondary"
          disabled={runningAll}
          onClick={() => void handleRunAll()}
          data-testid="run-all"
        >
          {runningAll ? '执行中…' : '▶ 全部运行'}
        </button>
        <button className="secondary" onClick={() => setShowTrace((v) => !v)}>
          轨迹
        </button>
      </div>
    </>
  )
}
