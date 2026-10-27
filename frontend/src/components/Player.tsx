/**
 * The film itself.
 *
 * A plain <video> with the browser's chrome replaced by something quieter:
 * a scrubber divided into the lesson's chapters, captions that brighten word
 * by word as the narrator says them (from the voice provider's timestamps),
 * and - when the lesson ends - the Lesson Planner's quiz.
 */

import { useCallback, useEffect, useImperativeHandle, useRef, useState, type PointerEvent, type Ref } from 'react'
import { api } from '../lib/api'
import { clock } from '../lib/format'
import type { CaptionCue, Manifest } from '../lib/types'
import { Quiz } from './Quiz'
import './player.css'

export interface PlayerHandle {
  seek: (seconds: number, play?: boolean) => void
}

interface Props {
  manifest: Manifest
  onTime?: (seconds: number) => void
  ref?: Ref<PlayerHandle>
}

const SPEEDS = [1, 1.25, 1.5, 0.75]

export function Player({ manifest, onTime, ref }: Props) {
  const video = useRef<HTMLVideoElement>(null)
  const shell = useRef<HTMLDivElement>(null)
  const bar = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [started, setStarted] = useState(false)
  const [ended, setEnded] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(manifest.duration)
  const [cues, setCues] = useState<CaptionCue[]>([])
  const [captions, setCaptions] = useState(true)
  const [speed, setSpeed] = useState(1)
  const [awake, setAwake] = useState(true)
  const [hover, setHover] = useState<number | null>(null)
  const sleep = useRef<number>(0)

  useEffect(() => {
    setStarted(false)
    setEnded(false)
    setTime(0)
    api
      .captions(manifest.captions_json)
      .then(setCues)
      .catch(() => setCues([]))
  }, [manifest.captions_json, manifest.video])

  // Smooth time while playing, for word-level captions and the scrubber.
  useEffect(() => {
    if (!playing) return
    let raf = 0
    const tick = () => {
      const t = video.current?.currentTime ?? 0
      setTime(t)
      onTime?.(t)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, onTime])

  const wake = useCallback(() => {
    setAwake(true)
    window.clearTimeout(sleep.current)
    sleep.current = window.setTimeout(() => setAwake(false), 2400)
  }, [])

  const play = useCallback(() => {
    const v = video.current
    if (!v) return
    setEnded(false)
    setStarted(true)
    void v.play()
  }, [])

  const toggle = useCallback(() => {
    const v = video.current
    if (!v) return
    if (v.paused) play()
    else v.pause()
  }, [play])

  const seek = useCallback(
    (seconds: number, andPlay = false) => {
      const v = video.current
      if (!v) return
      const t = Math.max(0, Math.min(seconds, (v.duration || duration) - 0.05))
      v.currentTime = t
      setTime(t)
      onTime?.(t)
      setEnded(false)
      if (andPlay) play()
    },
    [duration, onTime, play],
  )

  useImperativeHandle(ref, () => ({ seek }), [seek])

  const fraction = (clientX: number) => {
    const box = bar.current?.getBoundingClientRect()
    if (!box) return 0
    return Math.max(0, Math.min(1, (clientX - box.left) / box.width))
  }

  const onScrub = (event: PointerEvent<HTMLDivElement>) => {
    if (event.buttons !== 1 && event.type !== 'pointerdown') return
    ;(event.target as HTMLElement).setPointerCapture?.(event.pointerId)
    seek(fraction(event.clientX) * duration)
  }

  const fullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void shell.current?.requestFullscreen()
  }

  const onKey = (event: React.KeyboardEvent) => {
    const keys: Record<string, () => void> = {
      ' ': toggle,
      k: toggle,
      ArrowRight: () => seek(time + 5),
      ArrowLeft: () => seek(time - 5),
      c: () => setCaptions((c) => !c),
      f: fullscreen,
    }
    const action = keys[event.key]
    if (action) {
      event.preventDefault()
      action()
      wake()
    }
  }

  const cue = captions && started ? cues.find((c) => time >= c.start - 0.05 && time <= c.end + 0.15) : undefined
  const chapters = manifest.chapters.length ? manifest.chapters : [{ scene: 's1', title: manifest.title, start: 0, end: duration }]
  const current = chapters.find((c) => time >= c.start && time < c.end) ?? chapters[chapters.length - 1]
  const hoverChapter = hover === null ? null : chapters.find((c) => hover * duration >= c.start && hover * duration < c.end)
  const showChrome = !playing || awake

  return (
    <div
      ref={shell}
      className={`player${showChrome ? ' is-awake' : ''}${started ? ' is-started' : ''}`}
      tabIndex={0}
      onKeyDown={onKey}
      onPointerMove={wake}
      onPointerLeave={() => playing && setAwake(false)}
    >
      <video
        ref={video}
        className="player__video"
        src={manifest.video}
        poster={manifest.poster ?? undefined}
        preload="metadata"
        playsInline
        onClick={toggle}
        onPlay={() => {
          setPlaying(true)
          wake()
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false)
          setEnded(true)
        }}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || manifest.duration)}
        onTimeUpdate={(e) => !playing && setTime(e.currentTarget.currentTime)}
      />

      {cue ? (
        <p className="player__caption" aria-live="off">
          {cue.words.length
            ? cue.words.map(([word, start], i) => (
                <span key={i} className={time >= start - 0.02 ? 'is-said' : undefined}>
                  {word}{' '}
                </span>
              ))
            : cue.text}
        </p>
      ) : null}

      {!started ? (
        // The poster is the lesson's own title card, so the play control
        // waits in the corner rather than sitting on top of the title.
        <div className="player__start" onClick={play}>
          <button type="button" className="player__start-pill" onClick={play} aria-label="Play lesson">
            <span className="player__start-ring">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M8 5.5v13l10.5-6.5z" />
              </svg>
            </span>
            <span className="player__start-text">
              <b>Play the lesson</b>
              <span className="num">
                {chapters.length} chapters · {clock(duration)}
              </span>
            </span>
          </button>
        </div>
      ) : null}

      {ended && manifest.quiz.length ? <Quiz items={manifest.quiz} onReplay={() => seek(0, true)} /> : null}

      <div className="player__chrome">
        <div
          ref={bar}
          className="scrub"
          onPointerDown={onScrub}
          onPointerMove={(e) => {
            setHover(fraction(e.clientX))
            onScrub(e)
          }}
          onPointerLeave={() => setHover(null)}
          role="slider"
          aria-label="Seek"
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(time)}
        >
          {chapters.map((c) => {
            const span = Math.max(0.001, c.end - c.start)
            const done = Math.max(0, Math.min(1, (time - c.start) / span))
            return (
              <div key={c.scene} className={`scrub__seg${c === current ? ' is-current' : ''}`} style={{ flexGrow: span }}>
                <div className="scrub__fill" style={{ transform: `scaleX(${done})` }} />
              </div>
            )
          })}
          {hover !== null ? (
            <div className="scrub__tip num" style={{ left: `${hover * 100}%` }}>
              {clock(hover * duration)}
              {hoverChapter ? <span> · {hoverChapter.title}</span> : null}
            </div>
          ) : null}
        </div>

        <div className="player__row">
          <button type="button" className="pbtn" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              {playing ? <path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" /> : <path d="M8 5.5v13l10.5-6.5z" />}
            </svg>
          </button>
          <span className="player__time num">
            {clock(time)} <span>/ {clock(duration)}</span>
          </span>
          <span className="player__chapter">{current?.title}</span>
          <span className="player__spacer" />
          <button type="button" className={`pbtn pbtn--text${captions ? ' is-on' : ''}`} onClick={() => setCaptions((c) => !c)} aria-pressed={captions}>
            CC
          </button>
          <button
            type="button"
            className="pbtn pbtn--text num"
            onClick={() => {
              const next = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length]
              setSpeed(next)
              if (video.current) video.current.playbackRate = next
            }}
          >
            {speed}×
          </button>
          <a className="pbtn pbtn--text" href={manifest.video} download title="Download the lesson">
            MP4
          </a>
          <button type="button" className="pbtn" onClick={fullscreen} aria-label="Full screen">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" fill="none" stroke="currentColor" strokeWidth="1.8" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  )
}
