import type { NodeTypes } from '@xyflow/react'
import { PromptNode } from './PromptNode'
import { AssetNode } from './AssetNode'
import { ImageGenNode } from './ImageGenNode'
import { ImageEditNode } from './ImageEditNode'
import { ImageViewNode } from './ImageViewNode'
import { VideoGenNode } from './VideoGenNode'
import { VideoViewNode } from './VideoViewNode'
import { AudioGenNode } from './AudioGenNode'
import { BgmGenNode } from './BgmGenNode'
import { LipsyncNode } from './LipsyncNode'
import { AudioViewNode } from './AudioViewNode'
import { ComposeNode } from './ComposeNode'
import { GroupNode } from './GroupNode'

export const nodeTypes: NodeTypes = {
  prompt: PromptNode,
  asset: AssetNode,
  'image-gen': ImageGenNode,
  'image-edit': ImageEditNode,
  'image-view': ImageViewNode,
  'video-gen': VideoGenNode,
  'video-view': VideoViewNode,
  'audio-gen': AudioGenNode,
  'bgm-gen': BgmGenNode,
  lipsync: LipsyncNode,
  'audio-view': AudioViewNode,
  compose: ComposeNode,
  group: GroupNode,
}
