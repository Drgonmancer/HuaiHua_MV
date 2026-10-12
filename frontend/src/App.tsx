import { useCallback, useEffect, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type EdgeTypes,
  type OnNodeDrag,
} from '@xyflow/react'
import { useShallow } from 'zustand/react/shallow'
import '@xyflow/react/dist/style.css'
import { nodeTypes } from './nodes'
import { OrderedEdge } from './edges/OrderedEdge'
import { AddNodeMenu } from './ui/AddNodeMenu'
import { Toasts } from './ui/Toasts'
import { StatusBar } from './ui/StatusBar'
import { AgentPanel } from './ui/AgentPanel'
import { StudioPanel } from './ui/StudioPanel'
import { useFlowStore, createNode } from './store/flowStore'
import { useProjectStore } from './store/projectStore'
import { initPersistence } from './store/persist'
import { resumePolling } from './engine/executor'
import type { NodeKind, CanvasNode, CanvasEdge } from './types'

const edgeTypes: EdgeTypes = { ordered: OrderedEdge }

function isEditableTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  return (
    el.tagName === 'INPUT' ||
    el.tagName === 'TEXTAREA' ||
    el.tagName === 'SELECT' ||
    el.isContentEditable
  )
}

function CanvasInner() {
  const { screenToFlowPosition, getIntersectingNodes, fitView } = useReactFlow()
  const [menu, setMenu] = useState<{ screen: { x: number; y: number } } | null>(null)
  const [studioOpen, setStudioOpen] = useState(false)

  const {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    isValidConnection,
    addNode,
    assignParent,
    duplicateSelected,
    setViewport,
  } = useFlowStore(
    useShallow((s) => ({
      nodes: s.nodes,
      edges: s.edges,
      onNodesChange: s.onNodesChange,
      onEdgesChange: s.onEdgesChange,
      onConnect: s.onConnect,
      isValidConnection: s.isValidConnection,
      addNode: s.addNode,
      assignParent: s.assignParent,
      duplicateSelected: s.duplicateSelected,
      setViewport: s.setViewport,
    })),
  )

  const handleDoubleClick = useCallback((event: React.MouseEvent) => {
    const target = event.target as HTMLElement
    if (!target.classList.contains('react-flow__pane')) return
    setMenu({ screen: { x: event.clientX, y: event.clientY } })
  }, [])

  const handlePickNodeType = useCallback(
    (type: NodeKind) => {
      if (!menu) return
      const position = screenToFlowPosition(menu.screen)
      addNode(createNode(type, position))
      setMenu(null)
    },
    [menu, screenToFlowPosition, addNode],
  )

  const handleNodeDragStop: OnNodeDrag<CanvasNode> = useCallback(
    (_event, node) => {
      if (node.type === 'group') return
      const intersectingGroup = getIntersectingNodes(node).find(
        (n) => n.type === 'group',
      )
      assignParent(node.id, intersectingGroup ? intersectingGroup.id : null)
    },
    [getIntersectingNodes, assignParent],
  )

  useEffect(() => initPersistence(), [])

  useEffect(() => {
    const focusNodes = (event: Event) => {
      const ids = (event as CustomEvent<string[]>).detail
      setStudioOpen(false)
      window.requestAnimationFrame(() => void fitView({
        nodes: ids?.map((id) => ({ id })), padding: 0.16, duration: 250,
      }))
    }
    window.addEventListener('huaihua:focus-nodes', focusNodes)
    return () => window.removeEventListener('huaihua:focus-nodes', focusNodes)
  }, [fitView])

  const projectLoaded = useProjectStore((s) => s.loaded)
  useEffect(() => {
    if (projectLoaded) resumePolling()
  }, [projectLoaded])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd') {
        event.preventDefault()
        duplicateSelected()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [duplicateSelected])

  return (
    <div className="canvas-shell" onDoubleClick={handleDoubleClick}>
      <ReactFlow<CanvasNode, CanvasEdge>
        colorMode="dark"
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onNodeDragStop={handleNodeDragStop}
        onMoveEnd={(_e, viewport) => setViewport(viewport)}
        deleteKeyCode={['Backspace', 'Delete']}
        minZoom={0.2}
        maxZoom={2}
        zoomOnDoubleClick={false}
        selectionKeyCode="Shift"
        multiSelectionKeyCode={['Control', 'Meta']}
        defaultEdgeOptions={{ type: 'ordered' }}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={24} size={1} />
        <Controls position="bottom-left" />
        <MiniMap
          position="bottom-left"
          pannable
          zoomable
          style={{ marginBottom: 120 }}
        />
      </ReactFlow>

      {menu && (
        <AddNodeMenu
          screen={menu.screen}
          onPick={handlePickNodeType}
          onClose={() => setMenu(null)}
        />
      )}
      <Toasts />
      <StatusBar onOpenStudio={() => setStudioOpen(true)} />
      {studioOpen && <StudioPanel onClose={() => setStudioOpen(false)} />}
      <AgentPanel />
    </div>
  )
}

export default function App() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  )
}
