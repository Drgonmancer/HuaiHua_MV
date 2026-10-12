import { memo, useEffect, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'
import { runToNode } from '../engine/executor'
import { getVideoModels, type VideoModel } from '../api/videoModels'

const selectStyle: React.CSSProperties = {
  background: 'var(--canvas-panel-2)',
  color: 'var(--canvas-text)',
  border: '1px solid var(--canvas-border)',
  borderRadius: 6,
  padding: '4px 6px',
  fontSize: 12,
  outline: 'none',
  maxWidth: 150,
}

function fieldRow(label: string, control: React.ReactNode) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        marginBottom: 6,
      }}
    >
      <span style={{ fontSize: 12, color: 'var(--canvas-text-dim)', flexShrink: 0 }}>{label}</span>
      {control}
    </div>
  )
}

function VideoGenNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const updateNodeParams = useFlowStore((s) => s.updateNodeParams)
  const [models, setModels] = useState<VideoModel[]>([])
  useEffect(() => {
    void getVideoModels().then(setModels)
  }, [])

  const params = data.params
  const model =
    models.find((m) => m.public_model_id === params.model) ?? models[0]
  const duration = params.durationSeconds ?? 5
  const status = data.runtime.status
  const busy = status === 'queued' || status === 'running'
  const stale = status === 'stale'

  const ratios = model?.aspect_ratios?.length ? model.aspect_ratios : ['16:9', '9:16', '1:1']
  const durations = model?.verified_durations?.length ? model.verified_durations : [5, 10]
  const resolutions = model?.supported_resolutions?.length
    ? model.supported_resolutions
    : [model?.resolution ?? '720p']
  // 未显式选择时跟随模型默认档（Seedance 默认 720p；t2v 不接受 1080p）
  const currentResolution = params.resolution ?? model?.resolution ?? resolutions[0]
  useEffect(() => {
    if (!model) return
    const patch: CanvasNode['data']['params'] = {}
    if (params.model !== model.public_model_id) patch.model = model.public_model_id
    if (model.verified_durations?.length && !model.verified_durations.includes(duration)) {
      const choices = [...model.verified_durations].sort((a, b) => a - b)
      patch.durationSeconds = choices.find((choice) => choice >= duration) ?? choices[choices.length - 1]
    }
    if (model.aspect_ratios?.length && !model.aspect_ratios.includes(params.ratio ?? '16:9')) patch.ratio = model.aspect_ratios[0]
    if (model.supported_resolutions?.length && !model.supported_resolutions.includes(currentResolution)) patch.resolution = model.resolution ?? model.supported_resolutions[0]
    if (Object.keys(patch).length) updateNodeParams(id, patch)
  }, [id, model, params.model, params.ratio, currentResolution, duration, updateNodeParams])

  return (
    <NodeShell type="video-gen" selected={selected} title={data.title}>
      <div style={{ padding: 10 }}>
        {fieldRow(
          '模型',
          <select
            className="nodrag"
            style={selectStyle}
            value={model?.public_model_id ?? ''}
            onChange={(e) => updateNodeParams(id, { model: e.target.value })}
          >
            {models.map((m) => (
              <option key={m.public_model_id} value={m.public_model_id}>
                {m.name}
              </option>
            ))}
            {!models.length && <option value="">视频服务未配置</option>}
          </select>,
        )}
        {fieldRow(
          '比例',
          <select
            className="nodrag"
            style={selectStyle}
            value={params.ratio ?? '16:9'}
            onChange={(e) => updateNodeParams(id, { ratio: e.target.value })}
          >
            {ratios.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>,
        )}
        {fieldRow(
          '时长',
          <select
            className="nodrag"
            style={selectStyle}
            value={duration}
            onChange={(e) => updateNodeParams(id, { durationSeconds: Number(e.target.value) })}
          >
            {durations.map((d) => (
              <option key={d} value={d}>
                {d}s
              </option>
            ))}
          </select>,
        )}
        {fieldRow(
          '分辨率',
          <select
            className="nodrag"
            style={selectStyle}
            value={currentResolution}
            onChange={(e) => updateNodeParams(id, { resolution: e.target.value })}
          >
            {resolutions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>,
        )}
        <button
          className="nodrag canvas-generate-btn"
          disabled={busy}
          data-node-id={id}
          onClick={() => void runToNode(id)}
          style={{
            width: '100%',
            marginTop: 8,
            padding: '8px 12px',
            borderRadius: 8,
            border: 'none',
            background: busy ? 'var(--canvas-panel-2)' : 'var(--canvas-accent)',
            color: busy ? 'var(--canvas-text-dim)' : '#fff',
            fontSize: 13,
            fontWeight: 600,
            cursor: busy ? 'default' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
          }}
        >
          {busy ? '生成中…' : stale ? '重新生成（上游已变更）' : '开始生成'}
          <span style={{ fontSize: 11, opacity: 0.85 }}>按供应商计费</span>
        </button>
        {status === 'failed' && (
          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--canvas-danger)' }}>
            {data.runtime.error ?? '生成失败'}
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const VideoGenNode = memo(VideoGenNodeInner)
