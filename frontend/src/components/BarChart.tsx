import { useId, useState } from 'react'
import type { DayValue } from '@/lib/api'

/**
 * Минимальный столбчатый график для одного ряда (величина во времени).
 * Один акцентный цвет — оси/сетка приглушены, подпись только у наведённого
 * столбца, а не у каждого (иначе цифры сливаются в кашу).
 */
export function BarChart({
  data,
  formatValue,
  height = 120,
}: {
  data: DayValue[]
  formatValue: (value: number) => string
  height?: number
}) {
  const gradientId = useId()
  const [hovered, setHovered] = useState<number | null>(null)
  const max = Math.max(1, ...data.map((d) => d.value))
  const barWidth = 100 / data.length

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        className="h-[120px] w-full overflow-visible"
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.9" />
            <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0.5" />
          </linearGradient>
        </defs>
        {/* Базовая линия — единственный «оси» элемент, приглушённая. */}
        <line
          x1="0"
          y1={height - 1}
          x2="100"
          y2={height - 1}
          stroke="var(--color-border)"
          strokeWidth="0.5"
        />
        {data.map((d, i) => {
          const barHeight = (d.value / max) * (height - 8)
          const x = i * barWidth
          return (
            <rect
              key={d.date}
              x={x + barWidth * 0.15}
              y={height - barHeight}
              width={barWidth * 0.7}
              height={Math.max(barHeight, 1)}
              rx="1.5"
              fill={hovered === i ? 'var(--color-accent)' : `url(#${gradientId})`}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            />
          )
        })}
      </svg>
      {hovered !== null && (
        <div className="pointer-events-none absolute -top-8 rounded-2xl border bg-surface px-2 py-1 text-xs shadow-sm"
          style={{ left: `${hovered * barWidth}%` }}
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
