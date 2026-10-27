/**
 * What the stage shows before there is a lesson: a chain of rotating circles
 * - a Fourier series - drawing a closed curve, in the lessons' own palette.
 * It is the kind of picture the studio exists to make, playing quietly at
 * the size of the film that will replace it.
 */

import { useEffect, useRef } from 'react'

// Each term: [frequency, radius (fraction of height), phase]. Frequencies
// 1, -6 and 8 all differ by multiples of 7, so the drawing is a seven-petal rose.
const TERMS: [number, number, number][] = [
  [1, 0.2, 0],
  [-6, 0.035, 0],
  [8, 0.018, 0],
]
const STEP = 0.006
const PERIOD = Math.round((Math.PI * 2) / STEP)

export function IdleCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let frame = 0
    let raf = 0
    let trail: [number, number][] = []

    const arms = (t: number): [number, number, number][] => {
      const h = canvas.clientHeight
      let x = canvas.clientWidth * 0.66
      let y = h * 0.5
      const out: [number, number, number][] = []
      for (const [k, r, phase] of TERMS) {
        const radius = r * h * 1.35
        out.push([x, y, radius])
        x += radius * Math.cos(k * t + phase)
        y += radius * Math.sin(k * t + phase)
      }
      out.push([x, y, 0])
      return out
    }

    const advance = () => {
      const chain = arms(frame * STEP)
      const [x, y] = chain[chain.length - 1]
      trail.push([x, y])
      if (trail.length > PERIOD) trail.shift()
      frame += 1
    }

    const render = () => {
      const w = canvas.clientWidth
      const h = canvas.clientHeight
      ctx.clearRect(0, 0, w, h)
      const chain = arms(frame * STEP)
      ctx.lineWidth = 1
      for (let i = 0; i < chain.length - 1; i++) {
        const [x, y, radius] = chain[i]
        const [nx, ny] = chain[i + 1]
        ctx.strokeStyle = 'rgba(154, 160, 180, 0.18)'
        ctx.beginPath()
        ctx.arc(x, y, radius, 0, Math.PI * 2)
        ctx.stroke()
        ctx.strokeStyle = 'rgba(236, 238, 244, 0.42)'
        ctx.beginPath()
        ctx.moveTo(x, y)
        ctx.lineTo(nx, ny)
        ctx.stroke()
      }
      ctx.lineWidth = 2.2
      ctx.lineCap = 'round'
      for (let i = 1; i < trail.length; i++) {
        const a = i / trail.length
        ctx.strokeStyle = `rgba(88, 196, 221, ${0.05 + a * 0.85})`
        ctx.beginPath()
        ctx.moveTo(trail[i - 1][0], trail[i - 1][1])
        ctx.lineTo(trail[i][0], trail[i][1])
        ctx.stroke()
      }
      const [tx, ty] = chain[chain.length - 1]
      ctx.fillStyle = '#f7d96f'
      ctx.beginPath()
      ctx.arc(tx, ty, 3.6, 0, Math.PI * 2)
      ctx.fill()
    }

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = canvas.clientWidth * ratio
      canvas.height = canvas.clientHeight * ratio
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      trail = []
      if (reduced) {
        for (let i = 0; i < PERIOD; i++) advance()
        render()
      }
    }
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    resize()

    const loop = () => {
      advance()
      render()
      raf = requestAnimationFrame(loop)
    }
    if (!reduced) loop()
    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
    }
  }, [])

  return <canvas ref={canvasRef} className="idle__canvas" aria-hidden="true" />
}
