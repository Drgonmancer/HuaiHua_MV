/**
 * 数据流执行器：
 * - collectInputs：按 edge.data.order 排序收集上游产物（①② 语义）
 * - executeNode：单个生成节点 提交→轮询→写回 outputs→自动建视图节点（可等待）
 * - runToNode：拓扑序执行"自身+全部未完成/过期祖先"（AC-2 图→视频首帧自动注入）
 * - runAll：整图拓扑序执行（Pro Mode 用）
 * 并发：image 2 / video 1（信号量），轮询 image 2s / video 5s，退避封顶 15s。
 */
import { api, ApiError } from '../api/client'
import { useFlowStore, createNode, newId } from '../store/flowStore'
import { topoAncestors, topoSort, GEN_NODE_TYPES } from './graph'
import type { CanvasNode, NodeOutput, NodeKind } from '../types'
import { useProjectStore } from '../store/projectStore'

type JobKindName = 'image' | 'video' | 'audio' | 'compose'

const JOB_KIND: Partial<Record<NodeKind, JobKindName>> = {
  'image-gen': 'image',
  'video-gen': 'video',
  'audio-gen': 'audio',
  compose: 'compose',
}

const VIEW_TYPE: Partial<Record<NodeKind, NodeKind>> = {
  'image-gen': 'image-view',
  'video-gen': 'video-view',
  'audio-gen': 'audio-view',
  compose: 'video-view',
}

const POLL_INTERVAL_MS = { image: 2000, video: 5000, audio: 3000, compose: 3000 } as const
const MAX_POLL_INTERVAL_MS = 15000
const CONCURRENCY = { image: 2, video: 1, audio: 1, compose: 1 } as const

type RunJob = { kind: string; id: string }

const active = { image: 0, video: 0, audio: 0, compose: 0 }
const waiters: Record<JobKindName, (() => void)[]> = {
  image: [],
  video: [],
  audio: [],
  compose: [],
}
const pollingNodes = new Set<string>()

async function acquire(kind: JobKindName): Promise<void> {
  if (active[kind] < CONCURRENCY[kind]) {
    active[kind] += 1
    return
  }
  await new Promise<void>((resolve) => waiters[kind].push(resolve))
  active[kind] += 1
}

function release(kind: JobKindName) {
  active[kind] -= 1
  waiters[kind].shift()?.()
}

function getNode(nodeId: string): CanvasNode | undefined {
  return useFlowStore.getState().nodes.find((n) => n.id === nodeId)
}

/** 节点对外输出：gen → outputs；view → 上游 outputs 的选中项；prompt/asset → 静态值 */
export function resolveOutputs(node: CanvasNode): NodeOutput[] {
  const { nodes, edges } = useFlowStore.getState()
  switch (node.type) {
    case 'prompt': {
      const text = (node.data.value?.text ?? '').trim()
      return text ? [{ kind: 'text', text }] : []
    }
    case 'asset': {
      const url = (node.data.value?.url ?? '').trim()
      const kind = node.data.mediaType ?? 'image'
      return url ? [{ kind, url }] : []
    }
    case 'image-view':
    case 'video-view':
    case 'audio-view': {
      const edge = edges.find((e) => e.target === node.id && e.targetHandle === 'in:media')
      const upstream = edge ? nodes.find((n) => n.id === edge.source) : undefined
      const outputs = upstream ? resolveOutputs(upstream) : (node.data.outputs ?? [])
      const wanted = node.type === 'image-view' ? 'image' : node.type === 'audio-view' ? 'audio' : 'video'
      const media = outputs.filter((o) => o.kind === wanted && o.url)
      if (!media.length) return []
      const index = Math.min(node.data.selectedOutput ?? 0, media.length - 1)
      return [media[index]]
    }
    default:
      return node.data.outputs ?? []
  }
}

/** 按 targetHandle 分组、按序号排序收集上游产物 */
export function collectInputs(nodeId: string): Record<string, string[]> {
  const { nodes, edges } = useFlowStore.getState()
  const incoming = edges
    .filter((e) => e.target === nodeId)
    .sort((a, b) => (a.data?.order ?? 0) - (b.data?.order ?? 0))

  const grouped: Record<string, string[]> = {}
  for (const edge of incoming) {
    const source = nodes.find((n) => n.id === edge.source)
    if (!source) continue
    const handle = (edge.targetHandle ?? '').replace(/^in:/, '')
    const outputs = resolveOutputs(source)
    for (const output of outputs.slice(0, 1)) {
      const value = output.text ?? output.url ?? ''
      if (!value) continue
      grouped[handle] = grouped[handle] ?? []
      grouped[handle].push(value)
    }
  }
  return grouped
}

function setRuntime(nodeId: string, patch: Record<string, unknown>) {
  const node = getNode(nodeId)
  if (!node) return
  useFlowStore.getState().updateNodeData(nodeId, {
    runtime: { ...node.data.runtime, ...patch },
  })
}

/** 生成节点成功后：确保存在已连线的对应视图节点 */
function ensureViewNode(genNodeId: string) {
  const store = useFlowStore.getState()
  const gen = store.nodes.find((n) => n.id === genNodeId)
  if (!gen) return
  const viewType = VIEW_TYPE[gen.type as NodeKind]
  if (!viewType) return
  const hasView = store.edges.some((e) => {
    if (e.source !== genNodeId) return false
    const target = store.nodes.find((n) => n.id === e.target)
    return target?.type === viewType
  })
  if (hasView) return

  const view = createNode(viewType, {
    x: gen.position.x + (gen.width ?? 280) + 80,
    y: gen.position.y,
  })
  store.addNode(view)
  store.onConnect({
    source: genNodeId,
    target: view.id,
    sourceHandle: 'out',
    targetHandle: 'in:media',
  })
}

async function pollJobs(nodeId: string, jobs: RunJob[]): Promise<boolean> {
  if (pollingNodes.has(nodeId)) return false
  pollingNodes.add(nodeId)
  const outputsById = new Map<string, NodeOutput[]>()
  const failures: string[] = []
  let canceled = false
  let pending = jobs.map((j) => j.id)
  const kind = (jobs[0]?.kind ?? 'image') as JobKindName
  let interval: number = POLL_INTERVAL_MS[kind] ?? 2000
  const isCurrentRun = () => {
    const current = getNode(nodeId)
    return Boolean(current && ['queued', 'running'].includes(current.data.runtime.status)
      && jobs.every((job) => current.data.runtime.jobIds?.includes(job.id)))
  }

  try {
    while (pending.length) {
      await new Promise((resolve) => setTimeout(resolve, interval))
      if (!isCurrentRun()) return false

      const still: string[] = []
      for (const jobId of pending) {
        const jobKind = jobs.find((j) => j.id === jobId)?.kind ?? 'image'
        try {
          const result = await api<{
            job: { status: string; outputs: NodeOutput[]; error: string }
          }>(`/api/canvas/jobs/${jobKind}/${jobId}`)
          const job = result.job
          if (!isCurrentRun()) return false
          if (job.status === 'succeeded') {
            outputsById.set(jobId, job.outputs ?? [])
          } else if (job.status === 'failed' || job.status === 'canceled') {
            if (job.status === 'canceled') canceled = true
            failures.push(job.error || '生成失败')
          } else {
            still.push(jobId)
            if (job.status === 'running') setRuntime(nodeId, { status: 'running' })
          }
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) {
            failures.push('任务已过期')
          } else {
            still.push(jobId)
            interval = Math.min(interval * 2, MAX_POLL_INTERVAL_MS)
          }
        }
      }
      pending = still
    }
  } finally {
    pollingNodes.delete(nodeId)
  }

  const outputs: NodeOutput[] = []
  if (!isCurrentRun()) return false
  for (const job of jobs) {
    outputs.push(...(outputsById.get(job.id) ?? []))
  }

  if (outputs.length) {
    useFlowStore.getState().updateNodeData(nodeId, { outputs })
    setRuntime(nodeId, {
      status: 'succeeded',
      error: failures.length ? `部分失败：${failures[0]}` : undefined,
      finishedAt: new Date().toISOString(),
    })
    ensureViewNode(nodeId)
    if (failures.length) {
      useFlowStore
        .getState()
        .pushToast(`部分生成失败（成功 ${outputs.length} 个产物）`, 'error')
    }
    return true
  }
  setRuntime(nodeId, {
    status: canceled ? 'canceled' : 'failed',
    error: failures[0] ?? '生成失败',
    finishedAt: new Date().toISOString(),
  })
  return false
}

/** 执行单个生成节点，等待到成功/失败。返回是否成功。 */
export async function executeNode(nodeId: string): Promise<boolean> {
  const node = getNode(nodeId)
  if (!node) return false
  const kind = JOB_KIND[node.type as NodeKind]
  if (!kind) return true // 非生成节点视为已就绪
  const pushToast = useFlowStore.getState().pushToast

  const status = node.data.runtime.status
  if (status === 'queued' || status === 'running') return false

  const inputs = collectInputs(nodeId)
  if (node.type === 'compose') {
    if (!inputs.video?.length || !inputs.audio?.length) {
      pushToast('合成节点需要同时连接视频与音频输入', 'error')
      return false
    }
  } else {
    const textKey = node.type === 'audio-gen' ? 'text' : 'prompt'
    if (!inputs[textKey]?.length) {
      pushToast(
        node.type === 'audio-gen'
          ? '请先连接文本节点（提示词节点接到"文本"端口）'
          : '请先连接提示词节点（或在提示词节点里输入内容）',
        'error',
      )
      return false
    }
  }

  const clientRunId = newId('run')
  setRuntime(nodeId, {
    status: 'queued',
    jobKind: kind,
    clientRunId,
    error: undefined,
    startedAt: new Date().toISOString(),
  })

  await acquire(kind)
  try {
    const pendingNode = getNode(nodeId)
    if (!pendingNode || pendingNode.data.runtime.clientRunId !== clientRunId || pendingNode.data.runtime.status !== 'queued') return false
    const result = await api<{ jobs: RunJob[]; credits_cost: number }>(
      '/api/canvas/nodes/run',
      {
        method: 'POST',
        body: JSON.stringify({
          node_type: node.type,
          project_id: useProjectStore.getState().projectId,
          node_id: nodeId,
          client_run_id: clientRunId,
          params: node.data.params,
          inputs,
        }),
      },
    )
    const acceptedNode = getNode(nodeId)
    if (!acceptedNode || acceptedNode.data.runtime.clientRunId !== clientRunId || acceptedNode.data.runtime.status !== 'queued') {
      pushToast('旧内容任务已提交；当前编辑已保留，旧结果可在任务页查看。', 'info')
      return false
    }
    setRuntime(nodeId, {
      status: 'running',
      jobIds: result.jobs.map((j) => j.id),
      jobId: result.jobs[0]?.id,
      creditsCost: result.credits_cost,
    })
    return await pollJobs(nodeId, result.jobs)
  } catch (error) {
    const current = getNode(nodeId)
    if (!current || current.data.runtime.clientRunId !== clientRunId || current.data.runtime.status === 'stale') return false
    let message = '提交生成任务失败'
    if (error instanceof ApiError) {
      message = error.message
      if (error.status === 402) {
        const required = error.payload.required_credits
        message = `积分不足：本次需要 ${required ?? '?'} 积分`
      }
      if (error.status === 429) {
        message = error.message || '任务过多，请稍后再试'
      }
    }
    setRuntime(nodeId, { status: 'failed', error: message })
    pushToast(message, 'error')
    return false
  } finally {
    release(kind)
  }
}

/** 单节点运行（fire-and-forget，供节点按钮直接调用） */
export function runNode(nodeId: string): void {
  void executeNode(nodeId)
}

/** 运行到此节点：拓扑序执行 自身+全部未成功/过期 的生成类祖先 */
export async function runToNode(nodeId: string): Promise<void> {
  const { edges, pushToast } = useFlowStore.getState()
  let order: string[]
  try {
    order = topoAncestors(nodeId, edges)
  } catch (error) {
    pushToast((error as Error).message, 'error')
    return
  }

  for (const id of order) {
    const node = getNode(id)
    if (!node || !GEN_NODE_TYPES.has(node.type ?? '')) continue
    if (node.data.runtime.status === 'succeeded') continue
    const ok = await executeNode(id)
    if (!ok) {
      if (id !== nodeId) pushToast('上游节点未完成，已停止执行链', 'error')
      return
    }
  }
}

/** 一键跑全图：整图拓扑序执行全部生成类节点（跳过已成功） */
export async function runAll(): Promise<{ total: number; succeeded: number }> {
  const { nodes, edges, pushToast } = useFlowStore.getState()
  let order: string[]
  try {
    order = topoSort(
      nodes.map((n) => n.id),
      edges,
    )
  } catch (error) {
    pushToast((error as Error).message, 'error')
    return { total: 0, succeeded: 0 }
  }

  const genIds = order.filter((id) => {
    const node = getNode(id)
    return node && GEN_NODE_TYPES.has(node.type ?? '') && node.data.runtime.status !== 'succeeded'
  })
  let succeeded = 0
  const unsuccessful = new Set<string>()
  for (const id of genIds) {
    const dependencies = topoAncestors(id, edges).filter((dependency) => dependency !== id)
    if (dependencies.some((dependency) => unsuccessful.has(dependency))) {
      unsuccessful.add(id)
      setRuntime(id, { status: 'failed', error: '上游任务未完成，请重试上游后再运行此节点' })
      continue
    }
    if (await executeNode(id)) succeeded += 1
    else unsuccessful.add(id)
  }
  return { total: genIds.length, succeeded }
}

/** 项目加载后恢复未完成任务的轮询（running 已在序列化时降为 queued） */
export function resumePolling(): void {
  const { nodes } = useFlowStore.getState()
  for (const node of nodes) {
    const runtime = node.data.runtime
    const kind = JOB_KIND[node.type as NodeKind]
    if (
      kind &&
      (runtime.status === 'queued' || runtime.status === 'running') &&
      runtime.jobIds?.length
    ) {
      setRuntime(node.id, { status: 'running' })
      void pollJobs(
        node.id,
        runtime.jobIds.map((id) => ({ kind, id })),
      )
    } else if (kind && (runtime.status === 'queued' || runtime.status === 'running')) {
      if (runtime.clientRunId) void recoverSubmission(node.id, runtime.clientRunId)
      else setRuntime(node.id, { status: 'failed', error: '任务提交记录缺失，请检查任务页后重新运行' })
    }
  }
}

const recoveringSubmissions = new Set<string>()
async function recoverSubmission(nodeId: string, clientRunId: string): Promise<void> {
  if (recoveringSubmissions.has(nodeId)) return
  recoveringSubmissions.add(nodeId)
  try {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const current = getNode(nodeId)
      if (!current || current.data.runtime.clientRunId !== clientRunId || current.data.runtime.status === 'stale') return
      try {
        const receipt = await api<{jobs: RunJob[]}>(`/api/canvas/runs/${encodeURIComponent(clientRunId)}`)
        const latest = getNode(nodeId)
        if (!latest || latest.data.runtime.clientRunId !== clientRunId || latest.data.runtime.status === 'stale') return
        setRuntime(nodeId, {status: 'running', jobIds: receipt.jobs.map((job) => job.id), jobId: receipt.jobs[0]?.id})
        void pollJobs(nodeId, receipt.jobs)
        return
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) break
        if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 1000))
      }
    }
    const current = getNode(nodeId)
    if (current?.data.runtime.clientRunId === clientRunId) {
      setRuntime(nodeId, {status: 'failed', error: '未确认提交结果。请先检查任务页和供应商状态，再决定是否重新运行；系统未自动补发。'})
    }
  } finally {
    recoveringSubmissions.delete(nodeId)
  }
}
