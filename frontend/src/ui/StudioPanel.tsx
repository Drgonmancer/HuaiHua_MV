import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { studioApi, studioError } from '../api/studio'
import { createNode, newId, useFlowStore } from '../store/flowStore'
import { useProductionStore } from '../store/productionStore'
import { useProjectStore } from '../store/projectStore'
import { syncNow } from '../store/persist'
import { resumePolling, runToNode } from '../engine/executor'
import { buildExportRequest, canRetryNodeJob, createProduction, mediaForNode, mediaForShot, prepareStoryboardFragment, reconcileNodeJobs, reorderShots, synchronizeTimeline } from './studio/productionLogic'
import type { ProductionKind, ProductionShot, StudioAsset, StudioJob, StudioProject, StudioSettings, StudioTemplate } from '../studioTypes'
import './studio.css'

type Tab = 'projects' | 'brief' | 'cast' | 'assets' | 'storyboard' | 'timeline' | 'jobs' | 'settings'
const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'projects', label: '作品' }, { id: 'brief', label: '创意 / 剧本' },
  { id: 'cast', label: '角色 / 场景' }, { id: 'assets', label: '素材库' },
  { id: 'storyboard', label: '分镜' }, { id: 'timeline', label: '时间线' },
  { id: 'jobs', label: '任务' }, { id: 'settings', label: '配置' },
]
const JOB_LABEL: Record<StudioJob['status'], string> = {
  queued: '排队中', running: '处理中', succeeded: '已完成', failed: '失败', canceled: '已取消',
}
const KIND_LABEL: Record<string, string> = { image: '图片', video: '视频', audio: '音频', compose: '成片', export: '成片' }
const CATEGORIES = [
  ['product', '商品'], ['character', '角色'], ['scene', '场景'], ['reference', '参考'], ['video', '视频'], ['audio', '音频'],
]

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="studio-field"><span>{label}</span>{children}{hint ? <small>{hint}</small> : null}</label>
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="studio-empty">{children}</div>
}

function AssetReferences({ ids, assets, onChange }: { ids: string[]; assets: StudioAsset[]; onChange: (ids: string[]) => void }) {
  const images = assets.filter((asset) => asset.media_type === 'image')
  const missing = ids.filter((id) => !images.some((asset) => asset.id === id))
  return <fieldset className="studio-references">
    <legend>参考图片</legend>
    {images.length ? images.map((asset) => <label key={asset.id} className="studio-check">
      <input type="checkbox" checked={ids.includes(asset.id)} onChange={(event) => onChange(event.target.checked ? [...ids, asset.id] : ids.filter((id) => id !== asset.id))} />
      <span>{asset.name}</span>
    </label>) : <p className="studio-hint">在素材库上传商品、角色或场景参考图后选择。</p>}
    {missing.map((id) => <p className="studio-warning" key={id}>素材 {id} 已缺失 <button type="button" onClick={() => onChange(ids.filter((value) => value !== id))}>移除引用</button></p>)}
  </fieldset>
}

export function StudioPanel({ onClose }: { onClose: () => void }) {
  const production = useProductionStore((state) => state.production)
  const setProduction = useProductionStore((state) => state.setProduction)
  const updateProduction = useProductionStore((state) => state.updateProduction)
  const projectId = useProjectStore((state) => state.projectId)
  const title = useProjectStore((state) => state.title)
  const setTitle = useProjectStore((state) => state.setTitle)
  const nodes = useFlowStore((state) => state.nodes)
  const edges = useFlowStore((state) => state.edges)
  const [tab, setTab] = useState<Tab>(production ? 'brief' : 'projects')
  const [projects, setProjects] = useState<StudioProject[]>([])
  const [templates, setTemplates] = useState<StudioTemplate[]>([])
  const [assets, setAssets] = useState<StudioAsset[]>([])
  const [jobs, setJobs] = useState<StudioJob[]>([])
  const [settings, setSettings] = useState<StudioSettings | null>(null)
  const [voices, setVoices] = useState<Array<{ id: string; name: string }>>([])
  const [busy, setBusy] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [newKind, setNewKind] = useState<ProductionKind>('product')
  const [source, setSource] = useState<'template' | 'ai'>('template')
  const [episodeId, setEpisodeId] = useState('')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [uploadCategory, setUploadCategory] = useState('product')
  const [selectedShot, setSelectedShot] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<{ kind: 'asset' | 'project'; id: string; name: string } | null>(null)
  const [musicIds, setMusicIds] = useState<string[]>([])
  const [resolution, setResolution] = useState('1080p')
  const [fps, setFps] = useState(24)
  const fileInput = useRef<HTMLInputElement>(null)
  const panel = useRef<HTMLElement>(null)

  const reportError = useCallback((reason: unknown) => setError(studioError(reason)), [])
  const operate = async (name: string, action: () => Promise<void>) => {
    if (busy) return
    setBusy(name)
    setError('')
    setNotice('')
    try { await action() } catch (reason) { reportError(reason) } finally { setBusy('') }
  }

  useEffect(() => {
    let active = true
    panel.current?.focus()
    const requests = [studioApi.projects(), studioApi.templates(), studioApi.assets(), studioApi.settings(), studioApi.audioMetadata()] as const
    void Promise.allSettled(requests).then(([projectResult, templateResult, assetResult, settingsResult, voiceResult]) => {
      if (!active) return
      if (projectResult.status === 'fulfilled') setProjects(projectResult.value.projects)
      if (templateResult.status === 'fulfilled') setTemplates(templateResult.value.templates)
      if (assetResult.status === 'fulfilled') setAssets(assetResult.value.assets)
      if (settingsResult.status === 'fulfilled') setSettings(settingsResult.value)
      if (voiceResult.status === 'fulfilled') setVoices(voiceResult.value.voices.map((voice) => typeof voice === 'string'
        ? { id: voice, name: voice } : { id: voice.id ?? voice.voice_id ?? '', name: voice.name ?? voice.id ?? voice.voice_id ?? '' }).filter((voice) => voice.id))
      const failed = [projectResult, templateResult, assetResult, settingsResult].find((result) => result.status === 'rejected')
      if (failed?.status === 'rejected') reportError(failed.reason)
    })
    return () => { active = false }
  }, [reportError])

  useEffect(() => {
    if (tab !== 'jobs') return
    const controller = new AbortController()
    let inFlight = false
    const refresh = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const result = await studioApi.jobs(projectId, controller.signal)
        if (controller.signal.aborted) return
        setJobs(result.jobs)
        // Persist retried/canceled jobs back onto their actual nodes; a stale job must never overwrite a newer run.
        const flow = useFlowStore.getState()
        for (const node of flow.nodes) {
          const patch = reconcileNodeJobs(node, result.jobs)
          if (patch) flow.updateNodeData(node.id, patch)
        }
      } catch (reason) { if (!controller.signal.aborted) reportError(reason) } finally { inFlight = false }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [tab, projectId, reportError])

  const saveCurrent = async () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const beforeFlow = useFlowStore.getState()
      const beforeProduction = useProductionStore.getState().production
      const beforeTitle = useProjectStore.getState().title
      await syncNow()
      if (useProjectStore.getState().syncState !== 'online') throw new Error('当前作品尚未同步成功，已保留本地草稿。恢复服务后保存，再切换作品。')
      const afterFlow = useFlowStore.getState()
      if (beforeFlow.revision === afterFlow.revision && beforeFlow.viewport === afterFlow.viewport
        && beforeProduction === useProductionStore.getState().production && beforeTitle === useProjectStore.getState().title) return
    }
    throw new Error('保存期间内容仍在更新，已保留本地草稿。请等待当前编辑或任务结束后再保存和切换。')
  }

  const openProject = async (id: string) => {
    await saveCurrent()
    window.location.assign(`/tools/canvas/${encodeURIComponent(id)}`)
  }

  const editShot = (id: string, patch: Partial<ProductionShot>) => updateProduction((current) => {
    const next = { ...current, shots: current.shots.map((shot) => shot.id === id ? { ...shot, ...patch } : shot) }
    if (patch.dialogue !== undefined) next.timeline = next.timeline.map((clip) => clip.shot_id === id && clip.subtitle === current.shots.find((shot) => shot.id === id)?.dialogue ? { ...clip, subtitle: patch.dialogue! } : clip)
    return { ...next, timeline: synchronizeTimeline(next) }
  })

  const addShot = () => updateProduction((current) => {
    const shot: ProductionShot = {
      id: newId('shot'), episode_id: episodeId, title: `镜头 ${current.shots.length + 1}`, description: '',
      image_prompt: '', video_prompt: '', dialogue: '', character_ids: [], scene_id: '', duration_seconds: 4,
      order: current.shots.length + 1,
    }
    const next = { ...current, shots: [...current.shots, shot] }
    setSelectedShot(shot.id)
    return { ...next, timeline: synchronizeTimeline(next) }
  })

  const makeStoryboard = async () => {
    const current = useProductionStore.getState().production
    if (!current) throw new Error('请先创建作品')
    if (current.brief.trim().length < 4 && !current.shots.length) throw new Error('请填写至少 4 个字的创作需求，或手动添加分镜')
    if (source === 'ai' && settings && !settings.providers.text.configured) throw new Error('尚未配置文字供应商。可先使用本地模板，配置后再请求 AI 规划。')
    const result = await studioApi.storyboard(current, source, projectId ?? undefined, episodeId || undefined)
    if (useProductionStore.getState().production !== current) throw new Error('规划期间作品内容发生了修改，已保留当前编辑。请检查内容后重新搭建分镜。')
    const fragment = prepareStoryboardFragment(result, useFlowStore.getState().nodes)
    if (!fragment.nodes.length) throw new Error('服务未返回有效分镜节点')
    if (useFlowStore.getState().nodes.length) useFlowStore.getState().addFragment(fragment.nodes, fragment.edges)
    else useFlowStore.getState().setGraph(fragment.nodes, fragment.edges)
    const orderedProduction = { ...fragment.production, shots: [...fragment.production.shots].sort((a, b) => a.order - b.order).map((shot, index) => ({ ...shot, order: index + 1 })) }
    setProduction({ ...orderedProduction, timeline: synchronizeTimeline(orderedProduction) })
    setSelectedShot(fragment.production.shots[0]?.id ?? '')
    const plannedShotCount = new Set(fragment.nodes.map((node) => node.data.shot_id).filter(Boolean)).size
    setNotice(source === 'template' ? `已追加 ${plannedShotCount} 个镜头的本地模板草稿。请检查并编辑后运行节点。` : `已追加 AI 规划分镜：${plannedShotCount} 个镜头。媒体生成需运行对应节点。`)
    setTab('storyboard')
    await saveCurrent()
  }

  const runShots = async (shots: ProductionShot[]) => {
    if (running || busy) return
    const currentProduction = useProductionStore.getState().production
    if (shots.some((shot) => !shot.node_ids?.video && !shot.node_ids?.compose)) {
      setError('部分镜头尚未搭建，或内容已修改。请重新搭建分镜节点后再运行。')
      return
    }
    const targets = shots.flatMap((shot) => shot.node_ids?.compose ? [shot.node_ids.compose] : [shot.node_ids?.video, ...(shot.dialogue.trim() ? [shot.node_ids?.audio] : [])]).filter((id): id is string => Boolean(id))
    if (!targets.length) { setError('请先把分镜搭建为节点，再运行镜头'); return }
    setRunning(true)
    setError('')
    setNotice('镜头正在按依赖顺序执行，可在任务页查看状态。')
    try {
      await saveCurrent()
      for (const id of targets) {
        if (useProductionStore.getState().production !== currentProduction) throw new Error('运行期间作品内容发生修改，已停止后续镜头。请重新搭建分镜，再运行当前版本。')
        await runToNode(id)
      }
      const currentNodes = useFlowStore.getState().nodes
      const succeeded = targets.filter((id) => currentNodes.find((node) => node.id === id)?.data.runtime.status === 'succeeded').length
      setNotice(`镜头执行结束：${succeeded}/${targets.length} 个视频或配音节点成功。失败详情见节点和任务页。`)
    } catch (reason) { reportError(reason) } finally { setRunning(false) }
  }

  const applyAsset = (asset: StudioAsset, toShot: boolean) => {
    const flow = useFlowStore.getState()
    const node = createNode('asset', { x: flow.nodes.length ? Math.max(...flow.nodes.map((item) => item.position.x)) + 300 : 100, y: 180 }, {
      title: asset.name, mediaType: asset.media_type, value: { url: asset.url }, asset_id: asset.id,
    })
    flow.addNode(node)
    if (toShot && production) {
      const shot = production.shots.find((item) => item.id === selectedShot)
      if (!shot) { setError('请选择要应用素材的镜头'); return }
      if (asset.media_type === 'video' || asset.media_type === 'audio') {
        const ids: NonNullable<ProductionShot['node_ids']> = asset.media_type === 'video'
          ? { ...shot.node_ids, video: node.id, video_view: undefined, compose: undefined }
          : { ...shot.node_ids, audio: node.id, audio_view: undefined, compose: undefined }
        const videoSource = ids.video_view ?? ids.video
        const audioSource = ids.audio_view ?? ids.audio
        if (videoSource && audioSource) {
          const compose = createNode('compose', { x: node.position.x + 340, y: node.position.y }, {
            title: `${shot.title} · 音画合成`, shot_id: shot.id, params: { ratio: production.aspect_ratio },
          })
          flow.addNode(compose)
          flow.onConnect({ source: videoSource, target: compose.id, sourceHandle: 'out', targetHandle: 'in:video' })
          flow.onConnect({ source: audioSource, target: compose.id, sourceHandle: 'out', targetHandle: 'in:audio' })
          ids.compose = compose.id
        }
        editShot(shot.id, { node_ids: ids })
      }
      else {
        const target = shot.node_ids?.image
        if (!target) { setError('已加入素材节点；请先搭建分镜节点，再把参考图应用到镜头'); return }
        if (!flow.isValidConnection({ source: node.id, target, sourceHandle: 'out', targetHandle: 'in:ref' })) {
          setError('已加入素材节点；该镜头参考图端口已满或节点不存在，请在画布整理连线'); return
        }
        flow.onConnect({ source: node.id, target, sourceHandle: 'out', targetHandle: 'in:ref' })
      }
    }
    setNotice(toShot ? `「${asset.name}」已应用到镜头并加入画布` : `「${asset.name}」已加入画布`)
  }

  const submitExport = async () => {
    await saveCurrent()
    const current = useProductionStore.getState().production
    if (!current) throw new Error('请先创建作品')
    const flow = useFlowStore.getState()
    const scopedProduction = episodeId ? {
      ...current,
      shots: current.shots.filter((shot) => shot.episode_id === episodeId),
      timeline: current.timeline.filter((clip) => current.shots.some((shot) => shot.id === clip.shot_id && shot.episode_id === episodeId)),
    } : current
    const request = buildExportRequest(scopedProduction, useProjectStore.getState().projectId ?? '', flow.nodes, flow.edges)
    request.audio_urls = musicIds.map((id) => {
      const asset = assets.find((item) => item.id === id)
      if (!asset) throw new Error('背景音频素材已缺失，请重新选择')
      return asset.url
    })
    request.resolution = resolution
    request.fps = fps
    const result = await studioApi.export(request)
    setJobs((currentJobs) => [result.job, ...currentJobs.filter((job) => job.id !== result.job.id)])
    setNotice('成片任务已提交。完成后可在任务页预览并下载。')
    setTab('jobs')
  }

  const filteredShots = production?.shots.filter((shot) => !episodeId || shot.episode_id === episodeId).sort((a, b) => a.order - b.order) ?? []
  const focusStoryboard = () => {
    const ids = nodes.filter((node) => filteredShots.some((shot) => shot.id === node.data.shot_id)).map((node) => node.id)
    window.dispatchEvent(new CustomEvent('huaihua:focus-nodes', { detail: ids.length ? ids : nodes.map((node) => node.id) }))
  }
  const filteredAssets = assets.filter((asset) => (!category || asset.category === category) && (!query.trim() || asset.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())))
  const currentTemplates = templates.filter((template) => template.kind === production?.kind)
  const filteredTimeline = production?.timeline.filter((clip) => !episodeId || production.shots.some((shot) => shot.id === clip.shot_id && shot.episode_id === episodeId)).sort((a, b) => a.order - b.order) ?? []
  const timelineDuration = filteredTimeline.reduce((total, clip) => total + Math.max(0, clip.trim_end - clip.trim_start), 0)
  const detachedShots = production?.shots.filter((shot) => !shot.node_ids?.video && !shot.node_ids?.compose).length ?? 0

  return <aside ref={panel} tabIndex={-1} className="studio-panel nodrag" role="region" aria-label="槐花MV创作工作室" data-testid="studio-panel" onKeyDown={(event) => { if (event.key === 'Escape') onClose() }}>
    <header className="studio-header">
      <div><span className="studio-eyebrow">HUAIHUA · STUDIO</span><h2>创作工作室</h2></div>
      <button className="studio-icon-button" onClick={onClose} aria-label="关闭工作室" title="关闭工作室">×</button>
    </header>
    <div className="studio-project-strip"><span className="studio-tag">{production?.kind === 'comic' ? '漫剧' : production?.kind === 'product' ? '商品宣传片' : '自由画布'}</span><strong title={title}>{title}</strong><button disabled={Boolean(busy)} onClick={() => void operate('save', async () => { await saveCurrent(); setNotice('作品已保存并同步') })}>{busy === 'save' ? '保存中…' : '保存'}</button></div>
    <nav className="studio-tabs" aria-label="工作室功能">{TABS.map((item) => <button key={item.id} aria-current={tab === item.id ? 'page' : undefined} className={tab === item.id ? 'active' : ''} onClick={() => { setTab(item.id); setError(''); setDeleteTarget(null) }}>{item.label}{item.id === 'storyboard' && production?.shots.length ? <span>{production.shots.length}</span> : null}</button>)}</nav>
    <div className="studio-feedback" aria-live="polite">{error ? <p className="studio-error" role="alert">{error}<button aria-label="关闭错误提示" onClick={() => setError('')}>×</button></p> : null}{notice ? <p className="studio-notice">{notice}</p> : null}</div>
    <div className="studio-content">
      {tab === 'projects' ? <>
        <div className="studio-section-title"><h3>我的作品</h3><button disabled={Boolean(busy)} onClick={() => void operate('projects', async () => setProjects((await studioApi.projects()).projects))}>刷新</button></div>
        <form className="studio-card" onSubmit={(event) => { event.preventDefault(); void operate('create', async () => {
          if (!newTitle.trim()) throw new Error('请填写作品名称')
          await saveCurrent()
          const result = await studioApi.createProject({ title: newTitle.trim(), kind: newKind })
          window.location.assign(`/tools/canvas/${encodeURIComponent(result.project.id)}`)
        }) }}>
          <h4>创建独立作品</h4>
          <Field label="作品名称"><input value={newTitle} maxLength={80} placeholder="例如：山野茶饮春日宣传片" onChange={(event) => setNewTitle(event.target.value)} required /></Field>
          <Field label="创作类型"><select value={newKind} onChange={(event) => setNewKind(event.target.value as ProductionKind)}><option value="product">商品宣传片</option><option value="comic">漫剧 / 分集故事</option></select></Field>
          <button className="studio-primary" disabled={Boolean(busy) || running} type="submit">{busy === 'create' ? '创建中…' : '创建并打开'}</button>
          <p className="studio-hint">切换前会同步当前画布；每个作品拥有独立的节点、分镜与时间线。</p>
        </form>
        {!production ? <div className="studio-card"><h4>为当前画布启用制作流程</h4><p className="studio-hint">保留当前节点，把这个画布作为作品继续制作。</p><div className="studio-actions"><button onClick={() => { setProduction(createProduction('product')); setTab('brief') }}>商品宣传片</button><button onClick={() => { setProduction(createProduction('comic')); setTab('brief') }}>漫剧</button></div></div> : null}
        {projects.length ? <div className="studio-list">{projects.map((project) => <article className="studio-card studio-project-card" key={project.id}><div><span className="studio-tag">{project.production?.kind === 'comic' ? '漫剧' : project.production?.kind === 'product' ? '商品宣传片' : '画布'}</span><h4>{project.title}</h4><p>{project.production?.shots.length ?? project.nodes.length} {project.production ? '个镜头' : '个节点'} · {project.updated_at ? new Date(project.updated_at).toLocaleDateString('zh-CN') : '本地作品'}</p></div><div className="studio-actions"><button disabled={Boolean(busy) || running || project.id === projectId} onClick={() => void operate('open', () => openProject(project.id))}>{project.id === projectId ? '当前' : '打开'}</button><button className="studio-danger" disabled={Boolean(busy) || project.id === projectId} onClick={() => setDeleteTarget({ kind: 'project', id: project.id, name: project.title })}>删除</button></div></article>)}</div> : <Empty>暂时没有作品，创建一部新的宣传片或漫剧。</Empty>}
      </> : null}

      {tab === 'brief' && production ? <>
        <div className="studio-section-title"><h3>让创意落成镜头</h3><span>{production.kind === 'comic' ? '人物 · 情节 · 分集' : '商品 · 卖点 · 转化'}</span></div>
        <Field label="作品名称"><input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={80} /></Field>
        <Field label={production.kind === 'comic' ? '故事设定与本集目标' : '商品、受众与核心卖点'} hint="描述希望观众记住什么，补充产品细节、情节转折与发布渠道。"><textarea value={production.brief} rows={5} placeholder={production.kind === 'comic' ? '故事世界、人物关系、冲突与本集结尾…' : '商品名称、使用场景、卖点、目标用户和行动引导…'} onChange={(event) => updateProduction({ brief: event.target.value })} /></Field>
        <div className="studio-grid-two"><Field label="画面风格"><input value={production.style} onChange={(event) => updateProduction({ style: event.target.value })} /></Field><Field label="画面比例"><select value={production.aspect_ratio} onChange={(event) => updateProduction({ aspect_ratio: event.target.value })}><option>16:9</option><option>9:16</option><option>1:1</option><option>4:3</option></select></Field></div>
        <Field label="目标时长（秒）"><input type="number" min={1} max={3600} value={production.duration_seconds} onChange={(event) => updateProduction({ duration_seconds: Math.max(1, Number(event.target.value) || 1) })} /></Field>
        <Field label="完整剧本 / 文案"><textarea value={production.script} rows={8} placeholder="写下旁白、对白、场景与镜头想法；本地模板也可以继续编辑。" onChange={(event) => updateProduction({ script: event.target.value })} /></Field>
        <div className="studio-card"><h4>下一步：搭建可编辑分镜</h4><Field label="分镜来源"><select value={source} onChange={(event) => setSource(event.target.value as 'template' | 'ai')}><option value="template">本地模板草稿（无需供应商）</option><option value="ai">AI 规划（需要文字供应商）</option></select></Field><p className="studio-hint">{source === 'template' ? '模板是可编辑起点。创建节点后，图片、视频与配音需要分别运行。' : 'AI 根据创意、角色、场景与分集规划镜头。请求将使用你配置的文字供应商。'}</p>{currentTemplates.map((template) => <details key={template.id} className="studio-template"><summary>{template.name}</summary><p>{template.description}</p><ol>{template.steps.map((step) => <li key={step}>{step}</li>)}</ol></details>)}<button className="studio-primary" disabled={Boolean(busy) || running} onClick={() => void operate('storyboard', makeStoryboard)}>{busy === 'storyboard' ? '搭建中…' : source === 'template' ? '用模板搭建分镜节点' : '请求 AI 规划并搭建节点'}</button></div>
      </> : null}

      {tab === 'cast' && production ? <>
        <div className="studio-section-title"><h3>{production.kind === 'comic' ? '角色档案' : '商品 / 出镜角色'}</h3><button onClick={() => updateProduction((current) => ({ ...current, characters: [...current.characters, { id: newId('character'), name: '', description: '', reference_asset_ids: [], voice_id: '' }] }))}>＋ 添加</button></div>
        {production.characters.map((character) => <article key={character.id} className="studio-card"><div className="studio-card-top"><h4>角色档案</h4><button className="studio-danger" onClick={() => updateProduction((current) => ({ ...current, characters: current.characters.filter((item) => item.id !== character.id), shots: current.shots.map((shot) => ({ ...shot, character_ids: shot.character_ids.filter((id) => id !== character.id) })) }))}>移除</button></div><Field label="名称"><input value={character.name} placeholder="主角、配角或商品名称" onChange={(event) => updateProduction((current) => ({ ...current, characters: current.characters.map((item) => item.id === character.id ? { ...item, name: event.target.value } : item) }))} /></Field><Field label="外貌 / 形象 / 一致性描述"><textarea rows={3} value={character.description} onChange={(event) => updateProduction((current) => ({ ...current, characters: current.characters.map((item) => item.id === character.id ? { ...item, description: event.target.value } : item) }))} /></Field><Field label="配音 voice_id" hint={voices.length ? '可选择供应商声音或填写自定义 ID。' : '声音列表在配置供应商后可用；可先填写计划使用的 voice_id。'}><input list="studio-voices" value={character.voice_id} placeholder="例如：alloy" onChange={(event) => updateProduction((current) => ({ ...current, characters: current.characters.map((item) => item.id === character.id ? { ...item, voice_id: event.target.value } : item) }))} /></Field><AssetReferences ids={character.reference_asset_ids} assets={assets} onChange={(ids) => updateProduction((current) => ({ ...current, characters: current.characters.map((item) => item.id === character.id ? { ...item, reference_asset_ids: ids } : item) }))} /></article>)}
        {!production.characters.length ? <Empty>添加商品或人物档案，参考图会连接到对应分镜的图片节点。</Empty> : null}
        <datalist id="studio-voices">{voices.map((voice) => <option value={voice.id} key={voice.id}>{voice.name}</option>)}</datalist>
        <div className="studio-section-title"><h3>场景库</h3><button onClick={() => updateProduction((current) => ({ ...current, scenes: [...current.scenes, { id: newId('scene'), name: '', description: '', reference_asset_ids: [] }] }))}>＋ 添加</button></div>
        {production.scenes.map((scene) => <article className="studio-card" key={scene.id}><div className="studio-card-top"><h4>场景档案</h4><button className="studio-danger" onClick={() => updateProduction((current) => ({ ...current, scenes: current.scenes.filter((item) => item.id !== scene.id), shots: current.shots.map((shot) => shot.scene_id === scene.id ? { ...shot, scene_id: '' } : shot) }))}>移除</button></div><Field label="场景名称"><input value={scene.name} onChange={(event) => updateProduction((current) => ({ ...current, scenes: current.scenes.map((item) => item.id === scene.id ? { ...item, name: event.target.value } : item) }))} /></Field><Field label="空间 / 光线 / 氛围"><textarea rows={3} value={scene.description} onChange={(event) => updateProduction((current) => ({ ...current, scenes: current.scenes.map((item) => item.id === scene.id ? { ...item, description: event.target.value } : item) }))} /></Field><AssetReferences ids={scene.reference_asset_ids} assets={assets} onChange={(ids) => updateProduction((current) => ({ ...current, scenes: current.scenes.map((item) => item.id === scene.id ? { ...item, reference_asset_ids: ids } : item) }))} /></article>)}
        <div className="studio-section-title"><h3>分集管理</h3><button onClick={() => updateProduction((current) => ({ ...current, episodes: [...current.episodes, { id: newId('episode'), title: `第 ${current.episodes.length + 1} 集`, synopsis: '', script: '' }] }))}>＋ 添加分集</button></div>
        {production.episodes.map((episode) => <article key={episode.id} className="studio-card"><div className="studio-card-top"><h4>分集</h4><button className="studio-danger" onClick={() => { updateProduction((current) => ({ ...current, episodes: current.episodes.filter((item) => item.id !== episode.id), shots: current.shots.map((shot) => shot.episode_id === episode.id ? { ...shot, episode_id: '' } : shot) })); if (episodeId === episode.id) setEpisodeId('') }}>移除</button></div><Field label="集名"><input value={episode.title} onChange={(event) => updateProduction((current) => ({ ...current, episodes: current.episodes.map((item) => item.id === episode.id ? { ...item, title: event.target.value } : item) }))} /></Field><Field label="本集梗概"><textarea rows={2} value={episode.synopsis} onChange={(event) => updateProduction((current) => ({ ...current, episodes: current.episodes.map((item) => item.id === episode.id ? { ...item, synopsis: event.target.value } : item) }))} /></Field><Field label="本集剧本"><textarea rows={4} value={episode.script} onChange={(event) => updateProduction((current) => ({ ...current, episodes: current.episodes.map((item) => item.id === episode.id ? { ...item, script: event.target.value } : item) }))} /></Field></article>)}
      </> : null}

      {tab === 'assets' ? <>
        <div className="studio-section-title"><h3>可复用素材</h3><button disabled={Boolean(busy)} onClick={() => void operate('assets', async () => setAssets((await studioApi.assets()).assets))}>刷新</button></div>
        <form className="studio-asset-search" onSubmit={(event) => { event.preventDefault(); void operate('search', async () => setAssets((await studioApi.assets()).assets)) }}><Field label="搜索名称"><input value={query} placeholder="查找商品、人物、视频或音频" onChange={(event) => setQuery(event.target.value)} type="search" /></Field><Field label="分类"><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="">全部分类</option>{CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field></form>
        <div className="studio-upload"><Field label="上传到分类"><select value={uploadCategory} onChange={(event) => setUploadCategory(event.target.value)}>{CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field><input ref={fileInput} type="file" accept="image/*,video/*,audio/*" className="studio-hidden-input" aria-label="选择图片、视频或音频素材" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (!file) return; void operate('upload', async () => { const result = await studioApi.uploadAsset(file, file.name, uploadCategory); setAssets((current) => [result.asset, ...current]); setNotice(`「${result.asset.name}」已保存到本地素材库`) }) }} /><button className="studio-primary" disabled={Boolean(busy)} onClick={() => fileInput.current?.click()}>{busy === 'upload' ? '上传中…' : '上传图片 / 视频 / 音频'}</button><p className="studio-hint">上传文件由本地服务保存，可在不同作品复用。</p></div>
        {production?.shots.length ? <Field label="应用到哪个镜头"><select value={selectedShot} onChange={(event) => setSelectedShot(event.target.value)}><option value="">选择镜头</option>{[...production.shots].sort((a, b) => a.order - b.order).map((shot) => <option key={shot.id} value={shot.id}>{shot.order}. {shot.title}</option>)}</select></Field> : null}
        {filteredAssets.length ? <div className="studio-assets">{filteredAssets.map((asset) => <article key={asset.id} className="studio-asset-card"><div className="studio-asset-preview">{asset.media_type === 'image' ? <img src={asset.url} alt={asset.name} loading="lazy" /> : asset.media_type === 'video' ? <video controls preload="metadata" src={asset.url} aria-label={asset.name} /> : <audio controls preload="none" src={asset.url} aria-label={asset.name} />}</div><div className="studio-asset-info"><h4 title={asset.name}>{asset.name}</h4><span>{KIND_LABEL[asset.media_type]} · {CATEGORIES.find(([value]) => value === asset.category)?.[1] ?? asset.category}</span><div className="studio-actions"><button onClick={() => applyAsset(asset, false)}>加到画布</button>{selectedShot ? <button onClick={() => applyAsset(asset, true)}>应用到镜头</button> : null}<button className="studio-danger" disabled={Boolean(busy)} onClick={() => setDeleteTarget({ kind: 'asset', id: asset.id, name: asset.name })}>删除</button></div></div></article>)}</div> : <Empty>{query || category ? '没有匹配的素材。' : '上传第一份商品参考图、人物图片或本地视频，开始制作。'}</Empty>}
      </> : null}

      {tab === 'storyboard' && production ? <>
        <div className="studio-section-title"><h3>可编辑分镜</h3><button onClick={addShot}>＋ 镜头</button></div>
        {detachedShots ? <p className="studio-warning">{detachedShots} 个镜头尚未搭建，或内容修改后需要更新节点。重新搭建分镜，或为镜头应用新的本地素材后再运行和导出。</p> : null}
        {production.planning_source ? <p className="studio-source-label">当前分镜来源：{production.planning_source === 'template' ? '本地模板 / 手动编辑' : 'AI 规划 / 手动编辑'}</p> : null}
        <div className="studio-grid-two"><Field label="分集筛选"><select value={episodeId} onChange={(event) => setEpisodeId(event.target.value)}><option value="">全部分集</option>{production.episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title}</option>)}</select></Field><Field label="节点规划来源"><select value={source} onChange={(event) => setSource(event.target.value as 'template' | 'ai')}><option value="template">本地模板 / 手动分镜</option><option value="ai">AI 规划</option></select></Field></div>
        <div className="studio-actions studio-sticky-actions"><button className="studio-primary" disabled={Boolean(busy) || running} onClick={() => void operate('storyboard', makeStoryboard)}>{busy === 'storyboard' ? '搭建中…' : source === 'template' ? '搭建分镜节点' : '请求 AI 规划'}</button><button disabled={Boolean(busy) || running || !filteredShots.length} onClick={() => void runShots(filteredShots)}>{running ? '运行中…' : '运行当前分镜'}</button></div><p className="studio-hint">搭建会追加节点并保留原图。编辑分镜后需重新搭建节点，媒体任务由你手动运行。</p><div className="studio-actions"><button onClick={focusStoryboard} disabled={!nodes.length}>查看分镜画布</button></div>
        {filteredShots.map((shot) => {
          const video = mediaForShot(shot, nodes, edges)
          const image = mediaForNode(shot.node_ids?.image_view, 'image', nodes, edges) ?? mediaForNode(shot.node_ids?.image, 'image', nodes, edges)
          const orderedShots = [...production.shots].sort((a, b) => a.order - b.order)
          const shotIndex = orderedShots.findIndex((item) => item.id === shot.id)
          return <article key={shot.id} className="studio-card studio-shot" data-testid={`studio-shot-${shot.id}`}><div className="studio-card-top"><span className="studio-shot-number">{String(shotIndex + 1).padStart(2, '0')}</span><div className="studio-actions"><button aria-label={`上移 ${shot.title}`} onClick={() => updateProduction((current) => reorderShots(current, shot.id, -1))} disabled={shotIndex <= 0}>↑</button><button aria-label={`下移 ${shot.title}`} onClick={() => updateProduction((current) => reorderShots(current, shot.id, 1))} disabled={shotIndex >= production.shots.length - 1}>↓</button><button className="studio-danger" onClick={() => updateProduction((current) => { const next = { ...current, shots: current.shots.filter((item) => item.id !== shot.id).sort((a, b) => a.order - b.order).map((item, index) => ({ ...item, order: index + 1 })) }; return { ...next, timeline: synchronizeTimeline(next) } })}>移除</button></div></div>
            {video?.url ? <video className="studio-shot-preview" controls preload="metadata" src={video.url} /> : image?.url ? <img className="studio-shot-preview" src={image.url} alt={shot.title} loading="lazy" /> : <div className="studio-shot-placeholder">尚无媒体 · 先搭建，再运行或应用素材</div>}
            <Field label="镜头标题"><input value={shot.title} onChange={(event) => editShot(shot.id, { title: event.target.value })} /></Field>
            <div className="studio-grid-two"><Field label="时长（秒）"><input type="number" min={0.5} max={120} step={0.5} value={shot.duration_seconds} onChange={(event) => editShot(shot.id, { duration_seconds: Math.max(0.5, Number(event.target.value) || 0.5) })} /></Field><Field label="所属分集"><select value={shot.episode_id} onChange={(event) => editShot(shot.id, { episode_id: event.target.value })}><option value="">不分集</option>{production.episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title}</option>)}</select></Field></div>
            <Field label="画面 / 动作描述"><textarea value={shot.description} rows={2} onChange={(event) => editShot(shot.id, { description: event.target.value })} /></Field>
            <Field label="图片提示词"><textarea value={shot.image_prompt} rows={3} onChange={(event) => editShot(shot.id, { image_prompt: event.target.value })} /></Field>
            <Field label="视频提示词"><textarea value={shot.video_prompt} rows={3} onChange={(event) => editShot(shot.id, { video_prompt: event.target.value })} /></Field>
            <Field label="对白 / 旁白"><textarea value={shot.dialogue} rows={2} onChange={(event) => editShot(shot.id, { dialogue: event.target.value })} /></Field>
            <Field label="场景"><select value={shot.scene_id} onChange={(event) => editShot(shot.id, { scene_id: event.target.value })}><option value="">无场景引用</option>{production.scenes.map((scene) => <option key={scene.id} value={scene.id}>{scene.name || '未命名场景'}</option>)}</select></Field>
            {production.characters.length ? <fieldset className="studio-references"><legend>出镜角色 / 商品</legend>{production.characters.map((character) => <label className="studio-check" key={character.id}><input type="checkbox" checked={shot.character_ids.includes(character.id)} onChange={(event) => editShot(shot.id, { character_ids: event.target.checked ? [...shot.character_ids, character.id] : shot.character_ids.filter((id) => id !== character.id) })} /><span>{character.name || '未命名角色'}</span></label>)}</fieldset> : null}
            <div className="studio-actions"><button disabled={Boolean(busy) || running || !shot.node_ids?.video} onClick={() => void runShots([shot])}>运行此镜头</button><button onClick={() => { setSelectedShot(shot.id); setTab('assets') }}>应用本地素材</button></div>
          </article>
        })}
        {!filteredShots.length ? <Empty>从创意页创建模板分镜，或添加镜头并填写提示词。</Empty> : null}
      </> : null}

      {tab === 'timeline' && production ? <>
        <div className="studio-section-title"><h3>成片时间线</h3><span>{timelineDuration.toFixed(1)} 秒</span></div>
        <Field label="导出分集"><select value={episodeId} onChange={(event) => setEpisodeId(event.target.value)}><option value="">全部镜头 / 完整作品</option>{production.episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title}</option>)}</select></Field>
        <button disabled={!production.shots.length} onClick={() => updateProduction((current) => ({ ...current, timeline: synchronizeTimeline(current) }))}>同步分镜顺序</button><p className="studio-hint">按分镜顺序拼接，每个镜头选用一个视频。裁切和字幕可独立调整；上传的视频也可从素材库应用。</p>
        {filteredTimeline.map((clip) => {
          const shot = production.shots.find((item) => item.id === clip.shot_id)
          const output = mediaForShot(shot, nodes, edges)
          return <article key={clip.shot_id} className="studio-card studio-timeline-clip"><div className="studio-card-top"><h4>{clip.order}. {shot?.title ?? '已删除镜头'}</h4><span className={`studio-tag ${output?.url ? '' : 'warning'}`}>{output?.url ? '视频已就绪' : '缺少视频'}</span></div>{output?.url ? <video controls preload="metadata" className="studio-shot-preview" src={output.url} /> : null}<div className="studio-grid-two"><Field label="开始裁切（秒）"><input type="number" min={0} step={0.1} value={clip.trim_start} onChange={(event) => updateProduction((current) => ({ ...current, timeline: current.timeline.map((item) => item.shot_id === clip.shot_id ? { ...item, trim_start: Number(event.target.value) } : item) }))} /></Field><Field label="结束裁切（秒）"><input type="number" min={0.1} step={0.1} value={clip.trim_end} onChange={(event) => updateProduction((current) => ({ ...current, timeline: current.timeline.map((item) => item.shot_id === clip.shot_id ? { ...item, trim_end: Number(event.target.value) } : item) }))} /></Field></div><Field label="字幕"><textarea rows={2} value={clip.subtitle} onChange={(event) => updateProduction((current) => ({ ...current, timeline: current.timeline.map((item) => item.shot_id === clip.shot_id ? { ...item, subtitle: event.target.value } : item) }))} /></Field></article>
        })}
        {!filteredTimeline.length ? <Empty>创建分镜后，这里会显示对应的镜头片段。</Empty> : null}
        <div className="studio-card"><h4>导出设置</h4><fieldset className="studio-references"><legend>背景音频（全片混音）</legend>{assets.filter((asset) => asset.media_type === 'audio').map((asset) => <label className="studio-check" key={asset.id}><input type="checkbox" checked={musicIds.includes(asset.id)} onChange={(event) => setMusicIds((ids) => event.target.checked ? [...ids, asset.id] : ids.filter((id) => id !== asset.id))} /><span>{asset.name}</span></label>)}{!assets.some((asset) => asset.media_type === 'audio') ? <p className="studio-hint">可在素材库上传背景音乐，视频原音轨会保留。</p> : null}</fieldset><div className="studio-grid-two"><Field label="分辨率"><select value={resolution} onChange={(event) => setResolution(event.target.value)}><option>720p</option><option>1080p</option></select></Field><Field label="帧率"><select value={fps} onChange={(event) => setFps(Number(event.target.value))}><option value={24}>24 fps</option><option value={25}>25 fps</option><option value={30}>30 fps</option></select></Field></div><p className="studio-hint">画面比例：{production.aspect_ratio}。本地 FFmpeg 会归一化视频、拼接音轨并烧录字幕。</p>{settings && !settings.ffmpeg.available ? <p className="studio-warning">FFmpeg 尚未就绪，请在配置页查看。</p> : null}<button className="studio-primary" disabled={Boolean(busy) || !production.timeline.length} onClick={() => void operate('export', submitExport)}>{busy === 'export' ? '提交中…' : '合成并导出成片'}</button></div>
      </> : null}

      {tab === 'jobs' ? <>
        <div className="studio-section-title"><h3>制作任务</h3><button disabled={Boolean(busy)} onClick={() => void operate('jobs', async () => setJobs((await studioApi.jobs(projectId)).jobs))}>刷新</button></div><p className="studio-hint">当前作品的任务由服务保存；此页每 3 秒更新一次。重试会提交新的任务，请确认供应商配置。</p>
        {jobs.map((job) => <article className="studio-card" key={job.id}><div className="studio-card-top"><h4>{KIND_LABEL[job.kind] ?? job.kind} · {job.node_id ? nodes.find((node) => node.id === job.node_id)?.data.title ?? job.node_id : '成片导出'}</h4><span className={`studio-tag job-${job.status}`}>{JOB_LABEL[job.status]}</span></div><p className="studio-job-id">{job.id}</p>{job.error ? <p className="studio-error">{job.error}</p> : null}{job.outputs?.map((output, index) => output.url ? <div className="studio-job-output" key={`${job.id}-${index}`}>{output.kind === 'video' ? <video controls src={output.url} preload="metadata" /> : output.kind === 'audio' ? <audio controls src={output.url} preload="none" /> : output.kind === 'image' ? <img src={output.url} alt="任务输出" loading="lazy" /> : null}<a href={`${output.url}${output.url.includes('?') ? '&' : '?'}download=1`} download target="_blank" rel="noreferrer">下载{KIND_LABEL[output.kind] ?? '产物'}</a></div> : output.text ? <p key={index}>{output.text}</p> : null)}<div className="studio-actions">{['failed', 'canceled'].includes(job.status) ? <button disabled={Boolean(busy)} onClick={() => void operate(`retry-${job.id}`, async () => {
          const currentNode = useFlowStore.getState().nodes.find((item) => item.id === job.node_id)
          if (!canRetryNodeJob(currentNode, job)) throw new Error('此任务属于旧版本或节点正在执行新任务；请从当前镜头节点继续运行。')
          const result = await studioApi.retryJob(job.id)
          setJobs((current) => [result.job, ...current.filter((item) => item.id !== result.job.id)])
          const node = useFlowStore.getState().nodes.find((item) => item.id === result.job.node_id)
          if (node) {
            useFlowStore.getState().updateNodeData(node.id, { runtime: { ...node.data.runtime, status: result.job.status, jobId: result.job.id, jobIds: [result.job.id], error: result.job.error }, outputs: result.job.outputs ?? [] })
            resumePolling()
          }
          setNotice('重试任务已提交')
        })}>重试</button> : null}{['queued', 'running'].includes(job.status) ? <button className="studio-danger" disabled={Boolean(busy)} onClick={() => void operate(`cancel-${job.id}`, async () => { const result = await studioApi.cancelJob(job.id); setJobs((current) => current.map((item) => item.id === job.id ? result.job : item)); setNotice('已请求取消任务') })}>取消</button> : null}</div></article>)}
        {!jobs.length ? <Empty>当前没有制作任务。搭建分镜后运行镜头，或从时间线导出成片。</Empty> : null}
      </> : null}

      {tab === 'settings' ? <>
        <div className="studio-section-title"><h3>本地服务配置</h3><button disabled={Boolean(busy)} onClick={() => void operate('settings', async () => { setSettings(await studioApi.settings()); setNotice('配置状态已刷新') })}>刷新状态</button></div><p className="studio-hint">供应商由你在本机配置。此页面只显示配置状态和模型，不显示或收集密钥。</p>
        {settings ? <><div className="studio-list">{(['text', 'image', 'video', 'audio'] as const).map((kind) => <article className="studio-provider" key={kind}><div><h4>{kind === 'text' ? '文字 / AI 规划' : `${KIND_LABEL[kind]}生成`}</h4><p>{settings.providers[kind].model || '尚未指定模型'}</p></div><span className={`studio-tag ${settings.providers[kind].configured ? '' : 'warning'}`}>{settings.providers[kind].configured ? '已配置' : '未配置'}</span></article>)}<article className="studio-provider"><div><h4>FFmpeg</h4><p>本地视频拼接、音轨混音与字幕</p></div><span className={`studio-tag ${settings.ffmpeg.available ? '' : 'warning'}`}>{settings.ffmpeg.available ? '可用' : '不可用'}</span></article></div><div className="studio-card"><h4>配置文件位置</h4><code className="studio-config-path">{settings.config_path}</code><p className="studio-hint">在本机配置后重新启动服务，再点击刷新。未配置期间仍可编辑项目、使用模板、管理素材与制作本地视频成片。</p></div></> : <Empty>配置状态尚未加载，点击刷新状态重试。</Empty>}
      </> : null}

      {!production && ['brief', 'cast', 'storyboard', 'timeline'].includes(tab) ? <Empty>先在作品页创建商品宣传片或漫剧，或为当前画布启用制作流程。<button className="studio-primary" onClick={() => setTab('projects')}>打开作品页</button></Empty> : null}
      {deleteTarget ? <div className="studio-delete-confirm" role="alert"><strong>删除「{deleteTarget.name}」？</strong><p>{deleteTarget.kind === 'asset' ? '引用此素材的角色、场景和节点可能无法继续使用。' : '作品及其制作数据将被删除。'}</p><div className="studio-actions"><button onClick={() => setDeleteTarget(null)}>保留</button><button className="studio-danger" disabled={Boolean(busy)} onClick={() => void operate('delete', async () => {
        if (deleteTarget.kind === 'asset') { await studioApi.deleteAsset(deleteTarget.id); setAssets((current) => current.filter((asset) => asset.id !== deleteTarget.id)) }
        else { await studioApi.deleteProject(deleteTarget.id); setProjects((current) => current.filter((project) => project.id !== deleteTarget.id)) }
        setDeleteTarget(null); setNotice('已删除')
      })}>确认删除</button></div></div> : null}
    </div>
    <footer className="studio-footer"><span>创意 → 分镜 → 媒体 → 成片</span><span>{production?.shots.length ?? 0} 镜头 · {nodes.length} 节点</span></footer>
  </aside>
}
