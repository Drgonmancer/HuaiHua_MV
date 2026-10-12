import { memo, useRef, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CanvasNode } from '../types'
import { NodeShell } from './NodeShell'
import { useFlowStore } from '../store/flowStore'
import { api, ApiError } from '../api/client'

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

function AssetNodeInner({ id, data, selected }: NodeProps<CanvasNode>) {
  const updateNodeValue = useFlowStore((s) => s.updateNodeValue)
  const pushToast = useFlowStore((s) => s.pushToast)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const url = data.value?.url

  const handleFile = async (file: File | undefined) => {
    if (!file) return
    if (!file.type.startsWith('image/')) {
      pushToast('目前仅支持图片素材', 'error')
      return
    }
    setUploading(true)
    try {
      const dataUrl = await readFileAsDataUrl(file)
      // 先本地预览，托管成功后替换为持久 URL
      updateNodeValue(id, { url: dataUrl })
      const result = await api<{ url: string }>('/api/canvas/assets', {
        method: 'POST',
        body: JSON.stringify({ image: dataUrl }),
      })
      updateNodeValue(id, { url: result.url })
      pushToast('素材已上传', 'success')
    } catch (error) {
      const message =
        error instanceof ApiError && error.status === 401
          ? '未登录：素材仅本地预览，刷新后会丢失'
          : '上传失败，素材仅本地预览'
      pushToast(message, 'error')
    } finally {
      setUploading(false)
    }
  }

  return (
    <NodeShell type="asset" selected={selected} title={data.title} width={220}>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={(e) => void handleFile(e.target.files?.[0])}
      />
      <div
        className="nodrag"
        onClick={() => !uploading && fileInputRef.current?.click()}
        style={{
          minHeight: 160,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 12,
          overflow: 'hidden',
          cursor: uploading ? 'wait' : 'pointer',
        }}
        title={url ? '点击更换图片' : '点击上传图片'}
      >
        {url ? (
          <img
            src={url}
            alt={data.title}
            loading="lazy"
            style={{ width: '100%', display: 'block', borderRadius: 12 }}
          />
        ) : (
          <div style={{ textAlign: 'center', color: 'var(--canvas-text-dim)', padding: 20 }}>
            <div style={{ fontSize: 28, marginBottom: 8 }}>🖼</div>
            <div style={{ fontSize: 12 }}>{uploading ? '上传中…' : '点击上传参考图'}</div>
          </div>
        )}
      </div>
    </NodeShell>
  )
}

export const AssetNode = memo(AssetNodeInner)
