/**
 * 01 - Pose a concept.
 *
 * One generous text box, a place to drop notes, and a handful of quiet
 * choices. Everything else about the lesson is the agents' job.
 *
 * A dropped file is read at once by the studio's document parser (an MCP
 * tool) and screened by the guardrails, so the learner sees what the agents
 * will be given - title, pages, sections - before anything is made.
 */

import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { api, ApiError } from '../lib/api'
import type { DocumentPreview, Health, LessonOptions } from '../lib/types'
import { Card } from './Card'
import { Segmented } from './Segmented'
import { SpecularButton } from './SpecularButton'
import './compose.css'

const EXAMPLES = [
  'Why is e to the i pi equal to minus one?',
  'The derivative, as the slope of a curve at a single instant',
  'What does the determinant of a matrix actually measure?',
  'The Fourier series: building a square wave out of sine waves',
  "Bayes' theorem, told through a medical test",
  'Why the harmonic series diverges, even though its terms shrink to zero',
  'Eigenvectors: the directions a transformation only stretches',
  'The central limit theorem, and why bell curves are everywhere',
]

const SUGGESTIONS = [
  { label: 'The derivative', prompt: 'Explain the derivative as the slope of a curve at a single instant' },
  { label: 'Euler’s formula', prompt: 'Why does e^(i·θ) trace the unit circle, and why is e^(iπ) = −1?' },
  { label: 'Determinants', prompt: 'What does the determinant of a 2×2 matrix measure geometrically?' },
  { label: 'Fourier series', prompt: 'How a square wave is built from sine waves: the Fourier series' },
  { label: 'Pythagoras', prompt: 'A visual proof of the Pythagorean theorem by rearranging squares' },
  { label: 'Gradient descent', prompt: 'Gradient descent as rolling downhill on a loss surface, in one variable' },
]

const LANGUAGE_NAMES: Record<string, string> = {
  en: 'English',
  hi: 'हिन्दी · Hindi',
  te: 'తెలుగు · Telugu',
  ta: 'தமிழ் · Tamil',
  kn: 'ಕನ್ನಡ · Kannada',
  ml: 'മലയാളം · Malayalam',
  bn: 'বাংলা · Bengali',
  mr: 'मराठी · Marathi',
  gu: 'ગુજરાતી · Gujarati',
  od: 'ଓଡ଼ିଆ · Odia',
  pa: 'ਪੰਜਾਬੀ · Punjabi',
}

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.txt,.md'

interface Props {
  health: Health | null
  busy: boolean
  onSubmit: (prompt: string, options: LessonOptions, file: File | null) => Promise<void>
}

export function Compose({ health, busy, onSubmit }: Props) {
  const [prompt, setPrompt] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reasons, setReasons] = useState<string[]>([])
  const [preview, setPreview] = useState<DocumentPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [example, setExample] = useState(0)
  const [options, setOptions] = useState<LessonOptions>({
    audience: 'undergraduate',
    length: 'standard',
    language: 'en',
    voice: 'auto',
    quality: 'hd',
  })
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (health?.quality && ['draft', 'hd', 'hd60', 'preview'].includes(health.quality)) {
      setOptions((o) => ({ ...o, quality: health.quality as LessonOptions['quality'] }))
    }
  }, [health?.quality])

  useEffect(() => {
    if (prompt) return
    const timer = window.setInterval(() => setExample((n) => (n + 1) % EXAMPLES.length), 4200)
    return () => window.clearInterval(timer)
  }, [prompt])

  useEffect(() => {
    setPreview(null)
    if (!file) return
    let current = true
    setPreviewing(true)
    api
      .previewDocument(file)
      .then((result) => current && setPreview(result))
      .catch((exc) => current && setError(exc instanceof Error ? exc.message : String(exc)))
      .finally(() => current && setPreviewing(false))
    return () => {
      current = false
    }
  }, [file])

  const set = <K extends keyof LessonOptions>(key: K) => (value: LessonOptions[K]) => setOptions((o) => ({ ...o, [key]: value }))

  const needsKey = Boolean(file && (preview?.needs_key ?? health?.mode === 'replay'))

  const submit = async () => {
    if (busy || needsKey) return
    if (!prompt.trim() && !file) {
      setError('Describe a concept, or drop in notes to teach from.')
      setReasons([])
      return
    }
    setError(null)
    setReasons([])
    try {
      await onSubmit(prompt.trim(), options, file)
      setFile(null)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setReasons(exc instanceof ApiError ? exc.reasons : [])
    }
  }

  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      void submit()
    }
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files?.[0]
    if (dropped) setFile(dropped)
  }

  const providers = health?.providers
  const replay = health?.mode === 'replay'

  return (
    <Card
      index={1}
      className="compose"
      title="Pose a concept"
      subtitle="Name an idea, ask a question, or drop in your notes. A room of agents will analyse it, plan a lesson, animate it in Manim, narrate it and review every frame."
    >
      <div className="compose__grid">
        <div className="compose__field">
          <label className="visually-hidden" htmlFor="concept">
            Concept
          </label>
          <textarea
            id="concept"
            className="compose__input"
            value={prompt}
            onChange={(e) => {
              setPrompt(e.target.value)
              if (reasons.length) {
                setError(null)
                setReasons([])
              }
            }}
            onKeyDown={onKey}
            rows={3}
            maxLength={2000}
            placeholder={EXAMPLES[example]}
          />
          <div className="compose__suggest">
            <span className="compose__suggest-label">Try</span>
            {SUGGESTIONS.map((s) => (
              <button key={s.label} type="button" className="chip" onClick={() => setPrompt(s.prompt)}>
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <div
          className={`drop${dragging ? ' is-dragging' : ''}${file ? ' has-file' : ''}`}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => !file && fileInput.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && !file && fileInput.current?.click()}
          aria-label="Upload notes"
        >
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT}
            hidden
            onChange={(e) => {
              const picked = e.target.files?.[0]
              if (picked) setFile(picked)
              e.target.value = ''
            }}
          />
          {file ? (
            <div className="drop__file">
              <span className="drop__kind">{file.name.split('.').pop()?.toUpperCase()}</span>
              <span className="drop__name" title={file.name}>
                {file.name}
              </span>
              <span className="drop__size num">{(file.size / 1024).toFixed(0)} KB</span>
              <button
                type="button"
                className="drop__remove"
                onClick={(e) => {
                  e.stopPropagation()
                  setFile(null)
                }}
              >
                Remove
              </button>
            </div>
          ) : (
            <>
              <span className="drop__title">Drop notes here</span>
              <span className="drop__hint">A PDF, a photo of a whiteboard or a textbook page, or plain text. The Intake Reader will find the concept.</span>
            </>
          )}
        </div>
      </div>

      {file ? <DocumentCard preview={preview} loading={previewing} /> : null}

      <div className="compose__options">
        <Segmented
          label="Audience"
          value={options.audience}
          onChange={set('audience')}
          choices={[
            { value: 'school', label: 'School' },
            { value: 'undergraduate', label: 'Undergraduate' },
            { value: 'graduate', label: 'Graduate' },
          ]}
        />
        <Segmented
          label="Length"
          value={options.length}
          onChange={set('length')}
          choices={[
            { value: 'brief', label: '~1 min' },
            { value: 'standard', label: '2–3 min' },
            { value: 'deep', label: '4–5 min' },
          ]}
        />
        <div className="seg">
          <label className="seg__label" htmlFor="language">
            Narration
          </label>
          <select id="language" className="compose__select" value={options.language} onChange={(e) => set('language')(e.target.value)}>
            {(health?.languages ?? [{ code: 'en', name: 'English' }]).map((l) => (
              <option key={l.code} value={l.code}>
                {LANGUAGE_NAMES[l.code] ?? l.name}
              </option>
            ))}
          </select>
        </div>
        <Segmented
          label="Voice"
          value={options.voice}
          onChange={set('voice')}
          choices={[
            { value: 'auto', label: 'Auto' },
            { value: 'elevenlabs', label: 'ElevenLabs', disabled: providers ? !providers.elevenlabs : false, title: 'Add ELEVENLABS_API_KEY to .env' },
            { value: 'sarvam', label: 'Sarvam', disabled: providers ? !providers.sarvam : false, title: 'Add SARVAM_API_KEY to .env' },
            { value: 'edge', label: 'Free voice' },
            { value: 'silent', label: 'Captions only' },
          ]}
        />
        <Segmented
          label="Picture"
          value={options.quality}
          onChange={set('quality')}
          choices={[
            { value: 'draft', label: '720p' },
            { value: 'hd', label: '1080p' },
            { value: 'hd60', label: '1080p60' },
          ]}
        />
      </div>

      <div className="compose__action">
        <SpecularButton onClick={submit} disabled={busy || needsKey}>
          {busy ? 'The studio is working…' : needsKey ? 'Needs a Gemini key' : 'Make the lesson'}
          <span className="compose__kbd" aria-hidden="true">
            Ctrl ↵
          </span>
        </SpecularButton>
        <div className={`compose__note${error ? ' is-error' : ''}`} role={error ? 'alert' : undefined}>
          <p>
            {error
              ? error
              : needsKey
                ? 'Teaching from a file needs a Gemini key: the Intake Reader has to read it, and replay mode only has recorded lessons. Add GOOGLE_API_KEY to .env and restart the server, or remove the file.'
                : replay
                  ? 'Replay mode: no Gemini key yet, so the agents replay a recorded lesson - narration, rendering and review still run for real. Add GOOGLE_API_KEY to .env for live lessons.'
                  : 'Live: the agents think with Gemini on its free tier. A lesson takes a few minutes; you can watch them work below.'}
          </p>
          {reasons.length ? (
            <ul className="compose__reasons">
              {reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </Card>
  )
}

function DocumentCard({ preview, loading }: { preview: DocumentPreview | null; loading: boolean }) {
  if (loading || !preview) {
    return (
      <div className="docpreview is-loading" aria-live="polite">
        <span className="docpreview__eyebrow">Document parser · MCP</span>
        <p className="docpreview__title">Reading the file…</p>
      </div>
    )
  }
  const cleaned = preview.guardrail.action === 'sanitize' && preview.guardrail.categories.includes('indirect prompt injection')
  return (
    <div className="docpreview" aria-live="polite">
      <span className="docpreview__eyebrow">
        Document parser · MCP · {preview.kind === 'image' ? 'image' : `${preview.kind.toUpperCase()} · ${preview.pages} page${preview.pages === 1 ? '' : 's'}`}
      </span>
      <p className="docpreview__title">{preview.title || preview.name}</p>
      {preview.abstract ? <p className="docpreview__abstract">{preview.abstract}</p> : null}
      {preview.sections.length ? (
        <ul className="docpreview__sections">
          {preview.sections.slice(0, 8).map((section) => (
            <li key={section}>{section}</li>
          ))}
        </ul>
      ) : null}
      {preview.key_terms.length ? <p className="docpreview__terms">{preview.key_terms.slice(0, 8).join(' · ')}</p> : null}
      {cleaned ? (
        <p className="docpreview__flag">
          Guardrail: {preview.guardrail.reasons.length} instruction-like line{preview.guardrail.reasons.length === 1 ? '' : 's'} found in this file will be
          removed, and the agents will read it only as cleaned text.
        </p>
      ) : null}
      {preview.warnings.map((warning) => (
        <p key={warning} className="docpreview__warn">
          {warning}
        </p>
      ))}
    </div>
  )
}
