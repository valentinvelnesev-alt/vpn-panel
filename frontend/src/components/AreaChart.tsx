import { useId, useState } from 'react'
import type { DayValue } from '@/lib/api'

/**
 * Плавный график-площадь для одного ряда: тренд читается с одного взгляда,
 * точные цифры — по наведению.
 *
 * Кривая строится через monotone-кубический интерполятор: обычный сплайн
 * «выстреливает» ниже нуля на резких скачках, а выручка отрицательной
 * не бывает.
 */
export function AreaChart({
  data,
  formatValue,
  height = 140,
}: {
  data: DayValue[]
  formatValue: (value: number) => string
  height?: number
}) {
  const gradientId = useId()
  const [hovered, setHovered] = useState<number | null>(null)

  if (data.length === 0) return null

  const max = Math.max(1, ...data.map((d) => d.value))
  const step = 100 / Math.max(1, data.length - 1)
  const points = data.map((d, i) => ({
    x: i * step,
    y: height - 8 - (d.value / max) * (height - 24),
  }))

  // Кубическая кривая с горизонтальными касательными в точках — монотонная,
  // без выбросов за пределы данных.
  const line = points
    .map((p, i) => {
      if (i === 0) return `M ${p.x} ${p.y}`
      const prev = points[i - 1]
      const cx = (prev.x + p.x) / 2
      return `C ${cx} ${prev.y}, ${cx} ${p.y}, ${p.x} ${p.y}`
    })
    .join(' ')

  const area = `${line} L 100 ${height} L 0 ${height} Z`
  const active = hovered !== null ? points[hovered] : null

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        className="h-[140px] w-full overflow-visible"
        onMouseLeave={() => setHovered(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        <path d={area} fill={`url(#${gradientId})`} />
        <path
          d={line}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {active && (
          <>
            <line
              x1={active.x}
              y1={0}
              x2={active.x}
              y2={height}
              stroke="var(--color-border)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
            <circle
              cx={active.x}
              cy={active.y}
              r="4"
              fill="var(--color-accent)"
              stroke="var(--color-surface)"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          </>
        )}

        {/* Прозрачные полосы-мишени: попасть мышью в тонкую линию тяжело. */}
        {data.map((_, i) => (
          <rect
            key={i}
            x={i * step - step / 2}
            y={0}
            width={step}
            height={height}
            fill="transparent"
            onMouseEnter={() => setHovered(i)}
          />
        ))}
      </svg>

      {hovered !== null && (
        <div
          className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 whitespace-nowrap rounded-2xl border bg-surface px-2.5 py-1.5 text-xs shadow-lg"
          style={{ left: `${Math.min(92, Math.max(8, hovered * step))}%` }}
        >
          <span className="font-medium">{formatValue(data[hovered].value)}</span>
          <span className="ml-1.5 text-muted">
            {new Date(data[hovered].date).toLocaleDateString('ru-RU', {
              day: '2-digit',
              month: 'short',
            })}
          </span>
        </div>
      )}
    </div>
  )
}
