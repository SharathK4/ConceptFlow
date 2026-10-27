/**
 * The page. A masthead and three panels, in the order a person uses them:
 *
 *   pose a concept  ->  watch it being made, then watch it  ->  come back later
 *
 * The lesson being shown is reflected in the URL (?lesson=<id>), so a lesson
 * can be shared or reopened; it is still one page and one state transition.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from './lib/api'
import type { Health, LessonOptions, LessonSummary } from './lib/types'
import { useLesson } from './lib/useLesson'
import { Compose } from './components/Compose'
import { LightColumn } from './components/LightColumn'
import { Library } from './components/Library'
import { ProductionLog } from './components/ProductionLog'
import { Stage } from './components/Stage'
import './app.css'

function lessonFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get('lesson')
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [offline, setOffline] = useState(false)
  const [lessons, setLessons] = useState<LessonSummary[]>([])
  const [lessonId, setLessonId] = useState<string | null>(() => lessonFromUrl())
  const [logOpen, setLogOpen] = useState(false)
  const { production, detail } = useLesson(lessonId)

  const refresh = useCallback(() => {
    api
      .lessons()
      .then(setLessons)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    api
      .health()
      .then((h) => {
        setHealth(h)
        setOffline(false)
      })
      .catch(() => setOffline(true))
    refresh()
  }, [refresh])

  useEffect(() => {
    if (production.status === 'done' || production.status === 'failed') refresh()
  }, [production.status, refresh])

  useEffect(() => {
    const url = new URL(window.location.href)
    if (lessonId) url.searchParams.set('lesson', lessonId)
    else url.searchParams.delete('lesson')
    window.history.replaceState(null, '', url)
  }, [lessonId])

  useEffect(() => {
    const onPop = () => setLessonId(lessonFromUrl())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const submit = async (prompt: string, options: LessonOptions, file: File | null) => {
    const created = await api.create(prompt, options, file)
    setLessonId(created.id)
    setLogOpen(false)
    refresh()
    window.setTimeout(() => document.getElementById('stage')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
  }

  const open = (id: string) => {
    setLessonId(id)
    document.getElementById('stage')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const remove = async (id: string) => {
    await api.remove(id).catch(() => undefined)
    if (id === lessonId) setLessonId(null)
    refresh()
  }

  const busy = production.status === 'running' || production.status === 'queued'
  const p = health?.providers

  return (
    <main className={`cf${logOpen ? ' is-open' : ''}`}>
      <div className="cf__light">
        <LightColumn intensity={1} />
      </div>

      <header className="cf__masthead">
        <h1 className="cf__wordmark">ConceptFlow</h1>
        <div className="cf__under">
          <p className="cf__standfirst">
            Any mathematical idea, animated and narrated in the manner of a good lecture - made by a studio of AI agents: a CrewAI writers&apos; room
            inside a Google ADK workflow.
          </p>
          <p className="cf__status" aria-label="Services">
            {offline ? (
              <span className="cf__svc is-off">API offline - start the server</span>
            ) : (
              <>
                <Service on={!!p?.gemini} label={health?.mode === 'replay' ? 'Gemini · replay mode' : 'Gemini'} />
                <Service on={!!p?.elevenlabs} label="ElevenLabs" />
                <Service on={!!p?.sarvam} label="Sarvam" />
                <Service on={!!p?.latex} label="LaTeX" />
              </>
            )}
          </p>
        </div>
      </header>

      <div className="cf__panels">
        <Compose health={health} busy={busy} onSubmit={submit} />
        <div id="stage">
          <Stage lessonId={lessonId} production={production} detail={detail} onOpenLog={() => setLogOpen(true)} />
        </div>
        <Library lessons={lessons} current={lessonId} onOpen={open} onDelete={remove} />
      </div>

      <footer className="cf__foot">
        <span>ConceptFlow AI · Group 21</span>
        <span>Manim · Google ADK · CrewAI · Gemini · ElevenLabs · Sarvam AI</span>
      </footer>

      {logOpen && lessonId ? <ProductionLog production={production} detail={detail} onClose={() => setLogOpen(false)} /> : null}
    </main>
  )
}

function Service({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={`cf__svc${on ? ' is-on' : ''}`} title={on ? `${label}: configured` : `${label}: not configured`}>
      <i aria-hidden="true" />
      {label}
    </span>
  )
}
