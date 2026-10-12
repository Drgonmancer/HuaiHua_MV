import assert from 'node:assert/strict'
import { test, before } from 'node:test'
import { build } from 'esbuild'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

let engine
let flow

before(async () => {
  const root = fileURLToPath(new URL('../', import.meta.url))
  const built = await build({
    stdin: {
      contents: 'export * from "./src/engine/executor.ts"; export {useFlowStore,createNode} from "./src/store/flowStore.ts";',
      resolveDir: root,
      loader: 'ts',
    },
    bundle: true, write: false, platform: 'node', format: 'esm',
  })
  const temp = path.join(root, '.test-cache')
  await mkdir(temp, {recursive: true})
  const filename = path.join(temp, 'engine.mjs')
  await writeFile(filename, built.outputFiles[0].text)
  engine = await import(pathToFileURL(filename).href)
  flow = engine.useFlowStore
})

test('audio view passes upstream voice output to downstream composition', () => {
  const audio = engine.createNode('audio-gen', {x: 0, y: 0})
  audio.data.outputs = [{kind: 'audio', url: '/studio-media/voice.mp3'}]
  const view = engine.createNode('audio-view', {x: 300, y: 0})
  flow.getState().setGraph([audio, view], [{
    id: 'voice-edge', source: audio.id, target: view.id, targetHandle: 'in:media',
  }])
  assert.deepEqual(engine.resolveOutputs(view), audio.data.outputs)
})

test('batch execution does not submit video generation after its image ancestor fails', async () => {
  const prompt = engine.createNode('prompt', {x: 0, y: 0}, {value: {text: '主角站在街道上'}})
  const image = engine.createNode('image-gen', {x: 300, y: 0})
  const video = engine.createNode('video-gen', {x: 600, y: 0})
  flow.getState().setGraph([prompt, image, video], [
    {id: 'prompt-image', source: prompt.id, target: image.id, targetHandle: 'in:prompt'},
    {id: 'prompt-video', source: prompt.id, target: video.id, targetHandle: 'in:prompt'},
    {id: 'image-video', source: image.id, target: video.id, targetHandle: 'in:first'},
  ])
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (_url, request) => {
    requests.push(JSON.parse(request.body))
    return new Response(JSON.stringify({success: false, error: '图片服务未配置'}), {
      status: 503, headers: {'Content-Type': 'application/json'},
    })
  }
  try {
    const result = await engine.runAll()
    assert.equal(result.succeeded, 0)
    assert.equal(requests.length, 1, '缺失首帧的下游视频不应发起请求')
    const final = flow.getState().nodes.find(node => node.id === video.id)
    assert.match(final.data.runtime.error, /上游/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('a canceled media job keeps its canceled status on the node', async () => {
  const prompt = engine.createNode('prompt', {x: 0, y: 0}, {value: {text: '商品展示'}})
  const image = engine.createNode('image-gen', {x: 300, y: 0})
  flow.getState().setGraph([prompt, image], [{id: 'cancel-edge', source: prompt.id, target: image.id, targetHandle: 'in:prompt'}])
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (_url, request) => new Response(JSON.stringify(
    request.method === 'POST'
      ? {success: true, jobs: [{kind: 'image', id: 'cancel-job'}], credits_cost: 0}
      : {success: true, job: {status: 'canceled', outputs: [], error: '已取消'}}
  ), {status: 200, headers: {'Content-Type': 'application/json'}})
  try {
    assert.equal(await engine.executeNode(image.id), false)
    assert.equal(flow.getState().nodes.find(node => node.id === image.id).data.runtime.status, 'canceled')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('editing a prompt while its image is running invalidates the active result', () => {
  const prompt = engine.createNode('prompt', {x: 0, y: 0}, {value: {text: '原始描述'}})
  const image = engine.createNode('image-gen', {x: 300, y: 0})
  image.data.runtime = {status: 'running', jobIds: ['old-job']}
  flow.getState().setGraph([prompt, image], [{id: 'edit-edge', source: prompt.id, target: image.id, targetHandle: 'in:prompt'}])
  flow.getState().updateNodeValue(prompt.id, {text: '更新后的描述'})
  assert.equal(flow.getState().nodes.find(node => node.id === image.id).data.runtime.status, 'stale')
})

test('refresh recovery queries a lost receipt without submitting a second generation request', async () => {
  const image = engine.createNode('image-gen', {x: 0, y: 0})
  image.data.runtime = {status: 'queued', clientRunId: 'recover-run'}
  flow.getState().setGraph([image], [])
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, request) => {
    requests.push({url, method: request.method ?? 'GET'})
    return new Response(JSON.stringify(String(url).includes('/runs/')
      ? {success: true, jobs: [{kind: 'image', id: 'recovered-job'}]}
      : {success: true, job: {status: 'succeeded', outputs: [{kind: 'image', url: '/studio-media/recovered.png'}]}}
    ), {status: 200, headers: {'Content-Type': 'application/json'}})
  }
  try {
    engine.resumePolling()
    await new Promise(resolve => setTimeout(resolve, 2300))
    assert.equal(flow.getState().nodes.find(node => node.id === image.id).data.runtime.status, 'succeeded')
    assert.ok(requests.some(request => request.url.includes('/runs/')))
    assert.ok(requests.every(request => request.method === 'GET'))
  } finally { globalThis.fetch = originalFetch }
})

test('editing while submission is awaiting a response cannot attach the obsolete run', async () => {
  const prompt = engine.createNode('prompt', {x: 0, y: 0}, {value: {text: '旧提示词'}})
  const image = engine.createNode('image-gen', {x: 300, y: 0})
  flow.getState().setGraph([prompt, image], [{id: 'pending-edge', source: prompt.id, target: image.id, targetHandle: 'in:prompt'}])
  const originalFetch = globalThis.fetch
  let resolveSubmission
  globalThis.fetch = async (_url, request) => {
    if(request.method === 'POST') return new Promise(resolve => { resolveSubmission = resolve })
    return new Response(JSON.stringify({success:true,job:{status:'failed',outputs:[],error:'旧任务'}}),{status:200,headers:{'Content-Type':'application/json'}})
  }
  try {
    const pending = engine.executeNode(image.id)
    await new Promise(resolve => setTimeout(resolve, 20))
    flow.getState().updateNodeValue(prompt.id, {text:'新提示词'})
    resolveSubmission(new Response(JSON.stringify({success:true,jobs:[{kind:'image',id:'obsolete-job'}],credits_cost:0}),{status:200,headers:{'Content-Type':'application/json'}}))
    assert.equal(await pending, false)
    const runtime = flow.getState().nodes.find(node => node.id === image.id).data.runtime
    assert.equal(runtime.status,'stale')
    assert.ok(!runtime.jobIds?.includes('obsolete-job'))
  } finally { globalThis.fetch=originalFetch }
})
