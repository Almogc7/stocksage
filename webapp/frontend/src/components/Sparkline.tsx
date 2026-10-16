interface Props {
  closes: number[]
  width?: number
  height?: number
}

/** Plain inline SVG polyline — intentionally not a lightweight-charts
 * instance; a grid can have dozens of these at once. */
export function Sparkline({ closes, width = 120, height = 36 }: Props) {
  if (closes.length < 2) {
    return <svg width={width} height={height} />
  }

  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min || 1
  const stepX = width / (closes.length - 1)
  const up = closes[closes.length - 1] >= closes[0]

  const points = closes
    .map((c, i) => {
      const x = i * stepX
      const y = height - ((c - min) / span) * height
      return `${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <polyline
        points={points}
        fill="none"
        stroke={up ? 'var(--up)' : 'var(--down)'}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}
