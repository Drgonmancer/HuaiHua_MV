import type { NodeTypes } from '@xyflow/react'
import { PromptNode } from './PromptNode'
import { AssetNode } from './AssetNode'
import { ImageGenNode } from './ImageGenNode'
import { ImageViewNode } from './ImageViewNode'
import { VideoGenNode } from './VideoGenNode'
import { VideoViewNode } from './VideoViewNode'
import { AudioGenNode } from './AudioGenNode'
import { AudioViewNode } from './AudioViewNode'
import { ComposeNode } from './ComposeNode'
import { GroupNode } from './GroupNode'

export const nodeTypes: NodeTypes = {
  prompt: PromptNode,
  asset: AssetNode,
  'image-gen': ImageGenNode,
  'image-view': ImageViewNode,
  'video-gen': VideoGenNode,
  'video-view': VideoViewNode,
  'audio-gen': AudioGenNode,
  'audio-view': AudioViewNode,
  compose: ComposeNode,
  group: GroupNode,
}
