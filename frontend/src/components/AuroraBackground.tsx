import { useEffect, useRef } from 'react'

/**
 * Живой фон: медленно дрейфующие светящиеся частицы, соединённые линиями,
 * которые притягиваются к курсору.
 *
 * Рисуется на canvas, а не через DOM — сотни элементов с тенями положили бы
 * лэйаут. Всё в rAF с паузой при скрытой вкладке, чтобы не жечь батарею.
 * Уважает prefers-reduced-motion: при нём остаются только статичные точки.
 */

const PARTICLE_COUNT = 46
const LINK_DISTANCE = 150
const MOUSE_RADIUS = 190

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  radius: number
}

export default function AuroraBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    let width = 0
    let height = 0
    let particles: Particle[] = []
    const mouse = { x: -9999, y: -9999 }

    const resize = () => {
      width = canvas.clientWidth
      height = canvas.clientHeight
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    const seed = () => {
      particles = Array.from({ length: PARTICLE_COUNT }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.22,
        vy: (Math.random() - 0.5) * 0.22,
        radius: Math.random() * 1.7 + 0.9,
      }))
    }

    resize()
    seed()

    // Цвет берём из темы, чтобы фон менялся вместе с ней.
    const accent = getComputedStyle(document.documentElement)
      .getPropertyValue('--color-accent')
      .trim()

    const draw = () => {
      ctx.clearRect(0, 0, width, height)

      for (const p of particles) {
        if (!reduceMotion) {
          p.x += p.vx
          p.y += p.vy

          // Мягкое отталкивание от курсора — «расступаются» под мышкой.
          const dx = p.x - mouse.x
          const dy = p.y - mouse.y
          const dist = Math.hypot(dx, dy)
          if (dist < MOUSE_RADIUS && dist > 0.01) {
            const force = (1 - dist / MOUSE_RADIUS) * 0.6
            p.x += (dx / dist) * force
            p.y += (dy / dist) * force
          }

          // Заворачиваем по краям, чтобы поле не «выцветало».
          if (p.x < -20) p.x = width + 20
          if (p.x > width + 20) p.x = -20
          if (p.y < -20) p.y = height + 20
          if (p.y > height + 20) p.y = -20
        }

        ctx.beginPath()
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2)
        ctx.fillStyle = accent
        ctx.globalAlpha = 0.5
        ctx.fill()
      }

      // Линии между близкими частицами: чем ближе — тем ярче.
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const a = particles[i]
          const b = particles[j]
          const dist = Math.hypot(a.x - b.x, a.y - b.y)
          if (dist > LINK_DISTANCE) continue

          ctx.beginPath()
          ctx.moveTo(a.x, a.y)
          ctx.lineTo(b.x, b.y)
          ctx.strokeStyle = accent
          ctx.globalAlpha = (1 - dist / LINK_DISTANCE) * 0.16
          ctx.lineWidth = 1
          ctx.stroke()
        }
      }

      // Подсветка вокруг курсора — линии к ближайшим частицам ярче.
      for (const p of particles) {
        const dist = Math.hypot(p.x - mouse.x, p.y - mouse.y)
        if (dist > MOUSE_RADIUS) continue
        ctx.beginPath()
        ctx.moveTo(p.x, p.y)
        ctx.lineTo(mouse.x, mouse.y)
        ctx.strokeStyle = accent
        ctx.globalAlpha = (1 - dist / MOUSE_RADIUS) * 0.28
        ctx.lineWidth = 1
        ctx.stroke()
      }

      ctx.globalAlpha = 1
    }

    let frame = 0
    const loop = () => {
      draw()
      frame = requestAnimationFrame(loop)
    }
    loop()

    const onMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      mouse.x = e.clientX - rect.left
      mouse.y = e.clientY - rect.top
    }
    const onMouseLeave = () => {
      mouse.x = -9999
      mouse.y = -9999
    }
    const onResize = () => {
      resize()
      seed()
    }
    const onVisibility = () => {
      // Вкладка скрыта — останавливаем цикл, иначе rAF молотит вхолостую.
      cancelAnimationFrame(frame)
      if (!document.hidden) loop()
    }

    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseout', onMouseLeave)
    window.addEventListener('resize', onResize)
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseout', onMouseLeave)
      window.removeEventListener('resize', onResize)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      {/* Мягкие световые пятна под сеткой частиц — дают глубину. */}
      <div className="absolute -left-40 -top-40 size-[520px] rounded-full bg-accent/12 blur-[120px]" />
      <div className="absolute -bottom-52 -right-32 size-[560px] rounded-full bg-accent/10 blur-[130px]" />
      <canvas ref={canvasRef} className="size-full" />
    </div>
  )
}
