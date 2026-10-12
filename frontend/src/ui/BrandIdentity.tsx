const PERSONAL_TAGS = ['灵感捕手', '镜头玩家', '审美先行', '作品说话']

export function BrandIdentity() {
  return (
    <div className="canvas-brand" aria-label="槐花MV 品牌">
      <img
        className="canvas-brand-mark"
        src="/static/brand/huaihua-mark.svg"
        width={44}
        height={44}
        alt=""
      />
      <div className="canvas-brand-copy">
        <span className="canvas-brand-eyebrow">HUAIHUA · MOTION VISUAL</span>
        <h1>槐花<span>MV</span></h1>
      </div>
    </div>
  )
}

export function PersonalSignature() {
  return (
    <div className="canvas-personal-signature">
      <p>灵感有花期，创作不设限。</p>
      <ul className="canvas-personal-tags" aria-label="个性标签">
        {PERSONAL_TAGS.map((tag) => (
          <li key={tag}><span aria-hidden="true">#</span>{tag}</li>
        ))}
      </ul>
    </div>
  )
}
