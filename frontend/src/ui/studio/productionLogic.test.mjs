import test from 'node:test'
import assert from 'node:assert/strict'
import { synchronizeTimeline, reorderShots, prepareStoryboardFragment, buildExportRequest, reconcileProductionEdit, reconcileNodeJobs, canRetryNodeJob } from './productionLogic.ts'

const shot = (id, order, duration = 4) => ({
  id, order, duration_seconds: duration, episode_id: '', title: id, description: '',
  image_prompt: '', video_prompt: '', dialogue: '旁白', character_ids: [], scene_id: '',
  node_ids: { video_view: `${id}-view`, video: `${id}-video` },
})
const production = (shots) => ({
  kind: 'product', brief: '', style: '', aspect_ratio: '16:9', duration_seconds: 12,
  script: '', characters: [], scenes: [], episodes: [], shots, timeline: [],
})
const video = (id, url, status = 'succeeded') => ({
  id, type: 'video-gen', position: { x: 0, y: 0 },
  data: { title: id, params: {}, runtime: { status }, outputs: [{ kind: 'video', url }] },
})

test('timeline follows shot order and drops deleted shots while preserving edited trims and subtitles', () => {
  const p = production([shot('second', 2), shot('first', 1)])
  p.timeline = [
    { shot_id: 'removed', order: 1, trim_start: 0, trim_end: 3, subtitle: 'old' },
    { shot_id: 'second', order: 2, trim_start: 1, trim_end: 3, subtitle: 'edited' },
  ]
  assert.deepEqual(synchronizeTimeline(p), [
    { shot_id: 'first', order: 1, trim_start: 0, trim_end: 4, subtitle: '旁白' },
    { shot_id: 'second', order: 2, trim_start: 1, trim_end: 3, subtitle: 'edited' },
  ])
})

test('shortening a shot clamps saved trim ranges to a usable interval', () => {
  const p = production([shot('first', 1, 2)])
  p.timeline = [{ shot_id: 'first', order: 1, trim_start: 3, trim_end: 5, subtitle: '' }]
  const [clip] = synchronizeTimeline(p)
  assert.ok(clip.trim_start >= 0 && clip.trim_start < clip.trim_end)
  assert.equal(clip.trim_end, 2)
})

test('moving a shot preserves stable shot identity and synchronizes the timeline', () => {
  const p = production([shot('a', 1), shot('b', 2), shot('c', 3)])
  const moved = reorderShots(p, 'b', -1)
  assert.deepEqual(moved.shots.map((s) => [s.id, s.order]), [['b', 1], ['a', 2], ['c', 3]])
  assert.deepEqual(moved.timeline.map((c) => c.shot_id), ['b', 'a', 'c'])
  assert.deepEqual(p.shots.map((s) => s.id), ['a', 'b', 'c'])
})

test('appending storyboard remaps colliding IDs, preserves original graphs, and updates shot references', () => {
  const p = production([shot('a', 1)])
  const result = { production: p, nodes: [video('a-video', '/a.mp4'), video('a-view', '/b.mp4')],
    edges: [{ id: 'edge', source: 'a-video', target: 'a-view', targetHandle: 'in:media' }], title: 'draft' }
  const existing = [video('a-video', '/original.mp4')]
  const next = prepareStoryboardFragment(result, existing)
  assert.notEqual(next.nodes[0].id, 'a-video')
  assert.equal(next.edges[0].source, next.nodes[0].id)
  assert.equal(next.production.shots[0].node_ids.video, next.nodes[0].id)
  assert.ok(next.nodes[0].position.x > existing[0].position.x)
  assert.equal(existing[0].data.outputs[0].url, '/original.mp4')
})

test('repeated storyboard builds keep remapped node IDs within the persisted schema limit', () => {
  const id = 'n'.repeat(128)
  const result = { production: production([]), nodes: [video(id, '/new.mp4')], edges: [], title: '' }
  const fragment = prepareStoryboardFragment(result, [video(id, '/old.mp4')])
  assert.ok(fragment.nodes[0].id.length <= 128)
})

test('export selects video view outputs in timeline order instead of node creation order', () => {
  const p = production([shot('a', 1), shot('b', 2)])
  p.timeline = [{ shot_id: 'b', order: 1, trim_start: 1, trim_end: 3, subtitle: 'B' },
    { shot_id: 'a', order: 2, trim_start: 0, trim_end: 4, subtitle: 'A' }]
  const view = { id: 'a-view', type: 'video-view', position: { x: 0, y: 0 },
    data: { title: 'a', params: {}, runtime: { status: 'idle' }, selectedOutput: 1 } }
  const a = video('a-video', '/a0.mp4')
  a.data.outputs.push({ kind: 'video', url: '/a1.mp4' })
  const request = buildExportRequest(p, 'project-1', [video('b-video', '/b.mp4'), a, view],
    [{ id: 'edge', source: 'a-video', target: 'a-view', targetHandle: 'in:media' }])
  assert.deepEqual(request.clips.map((clip) => [clip.url, clip.subtitle]), [['/b.mp4', 'B'], ['/a1.mp4', 'A']])
})

test('export refuses missing, stale, or invalidly trimmed shots instead of silently omitting them', () => {
  const p = production([shot('a', 1)])
  p.timeline = [{ shot_id: 'a', order: 1, trim_start: 3, trim_end: 1, subtitle: '' }]
  assert.throws(() => buildExportRequest(p, 'p', [video('a-video', '/a.mp4')], []), /裁切/)
  p.timeline[0].trim_end = 4
  assert.throws(() => buildExportRequest(p, 'p', [video('a-video', '/a.mp4', 'stale')], []), /视频/)
  assert.throws(() => buildExportRequest(p, 'p', [], []), /视频/)
})

test('export requires the composed clip when a shot has voice composition', () => {
  const p = production([shot('a', 1)])
  p.shots[0].node_ids.compose = 'a-composed'
  p.timeline = synchronizeTimeline(p)
  const raw = video('a-video', '/silent.mp4')
  assert.throws(() => buildExportRequest(p, 'p', [raw], []), /视频/)
  const composed = { ...video('a-composed', '/voice.mp4'), type: 'compose' }
  const request = buildExportRequest(p, 'p', [raw, composed], [])
  assert.equal(request.clips[0].url, '/voice.mp4')
})

test('editing a generated shot prompt detaches its previous output and blocks exporting the old video', () => {
  const before = production([shot('a', 1)])
  before.timeline = synchronizeTimeline(before)
  const after = reconcileProductionEdit(before, { ...before, shots: [{ ...before.shots[0], video_prompt: 'new camera move' }] })
  assert.equal(after.shots[0].node_ids, undefined)
  assert.throws(() => buildExportRequest(after, 'p', [video('a-video', '/old.mp4')], []), /视频/)
  assert.equal(before.shots[0].node_ids.video, 'a-video')
})

test('referenced character voice, scene image, episode script and global style edits invalidate affected shots', () => {
  const before = production([shot('a', 1), shot('b', 2)])
  before.characters = [{ id: 'hero', name: 'Hero', description: '', reference_asset_ids: [], voice_id: 'old' }]
  before.scenes = [{ id: 'scene', name: 'Garden', description: '', reference_asset_ids: ['old-image'] }]
  before.episodes = [{ id: 'ep', title: 'Episode', synopsis: '', script: 'before' }]
  before.shots[0].character_ids = ['hero']
  before.shots[0].scene_id = 'scene'
  before.shots[0].episode_id = 'ep'
  const changes = [
    { characters: [{ ...before.characters[0], voice_id: 'new' }] },
    { scenes: [{ ...before.scenes[0], reference_asset_ids: ['new-image'] }] },
    { episodes: [{ ...before.episodes[0], script: 'after' }] },
  ]
  for (const patch of changes) {
    const after = reconcileProductionEdit(before, { ...before, ...patch })
    assert.equal(after.shots[0].node_ids, undefined)
    assert.equal(after.shots[1].node_ids.video, 'b-video')
  }
  const restyled = reconcileProductionEdit(before, { ...before, style: 'new style' })
  assert.ok(restyled.shots.every((entry) => entry.node_ids === undefined))
})

test('ordering, subtitles and unused cast edits retain outputs and applying a replacement local asset remains possible', () => {
  const before = production([shot('a', 1), shot('b', 2)])
  before.timeline = synchronizeTimeline(before)
  const reordered = reconcileProductionEdit(before, reorderShots(before, 'b', -1))
  assert.equal(reordered.shots[0].node_ids.video, 'b-video')
  const renamedSubtitle = reconcileProductionEdit(before, { ...before, timeline: before.timeline.map((clip) => ({ ...clip, subtitle: 'new caption' })) })
  assert.equal(renamedSubtitle.shots[0].node_ids.video, 'a-video')
  const newCast = reconcileProductionEdit(before, { ...before, characters: [{ id: 'unused', name: 'Unused', description: '', reference_asset_ids: [], voice_id: '' }] })
  assert.equal(newCast.shots[0].node_ids.video, 'a-video')
  const replacement = reconcileProductionEdit(before, { ...before, shots: [{ ...before.shots[0], node_ids: { video: 'local-video' } }, before.shots[1]] })
  assert.equal(replacement.shots[0].node_ids.video, 'local-video')
})

test('job reconciliation tracks normal executor jobIds and retains all successful outputs in requested order', () => {
  const node = video('node', '', 'running')
  node.data.runtime.jobIds = ['first', 'second']
  const patch = reconcileNodeJobs(node, [
    { id: 'second', node_id: 'node', kind: 'video', status: 'succeeded', outputs: [{ kind: 'video', url: '/2.mp4' }] },
    { id: 'first', node_id: 'node', kind: 'video', status: 'succeeded', outputs: [{ kind: 'video', url: '/1.mp4' }] },
  ])
  assert.equal(patch.runtime.status, 'succeeded')
  assert.deepEqual(patch.outputs.map((output) => output.url), ['/1.mp4', '/2.mp4'])
})

test('old, incomplete or stale job snapshots cannot replace newer node output', () => {
  const node = video('node', '/latest.mp4', 'running')
  node.data.runtime.jobIds = ['current']
  const old = { id: 'old', node_id: 'node', kind: 'video', status: 'succeeded', outputs: [{ kind: 'video', url: '/old.mp4' }] }
  assert.equal(reconcileNodeJobs(node, [old]), null)
  node.data.runtime.status = 'stale'
  assert.equal(reconcileNodeJobs(node, [{ ...old, id: 'current' }]), null)
  node.data.runtime.status = 'running'
  node.data.runtime.jobIds.push('missing')
  assert.equal(reconcileNodeJobs(node, [{ ...old, id: 'current' }]), null)
})

test('retrying an old failed task cannot supersede the current node run', () => {
  const node = video('node', '/latest.mp4', 'running')
  node.data.runtime.jobIds = ['new-job']
  const old = { id: 'old-job', node_id: 'node', kind: 'video', status: 'failed' }
  assert.equal(canRetryNodeJob(node, old), false)
  node.data.runtime.status = 'succeeded'
  assert.equal(canRetryNodeJob(node, old), false)
  node.data.runtime.status = 'failed'
  const current = {...old, id: 'new-job'}
  assert.equal(canRetryNodeJob(node, current), true)
})
