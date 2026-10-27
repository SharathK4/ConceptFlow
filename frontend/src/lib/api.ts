import type {
  CaptionCue,
  DocumentPreview,
  Health,
  LessonDetail,
  LessonOptions,
  LessonSummary,
  Metrics,
  ProvenanceRecord,
  TraceData,
} from './types'

/** An API refusal, carrying the guardrail's reasons when there are any. */
export class ApiError extends Error {
  reasons: string[]

  constructor(message: string, reasons: string[] = []) {
    super(message)
    this.reasons = reasons
  }
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    let reasons: string[] = []
    try {
      const body = await response.json()
      const detail = body?.detail
      if (typeof detail === 'string') message = detail
      else if (detail?.message) {
        message = detail.message
        reasons = Array.isArray(detail.reasons) ? detail.reasons : []
      } else if (detail) message = JSON.stringify(detail)
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, reasons)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => fetch('/api/health').then((r) => json<Health>(r)),
  lessons: () => fetch('/api/lessons').then((r) => json<LessonSummary[]>(r)),
  lesson: (id: string) => fetch(`/api/lessons/${id}`).then((r) => json<LessonDetail>(r)),
  remove: (id: string) => fetch(`/api/lessons/${id}`, { method: 'DELETE' }).then((r) => json<{ deleted: string }>(r)),
  captions: (url: string) => fetch(url).then((r) => json<CaptionCue[]>(r)),
  metrics: (id: string) => fetch(`/api/lessons/${id}/metrics`).then((r) => json<Metrics>(r)),
  trace: (id: string) => fetch(`/api/lessons/${id}/trace`).then((r) => json<TraceData>(r)),
  provenance: (id: string) => fetch(`/api/lessons/${id}/provenance`).then((r) => json<ProvenanceRecord[]>(r)),
  reportUrl: (id: string, format: 'json' | 'md') => `/api/lessons/${id}/report${format === 'md' ? '?format=md' : ''}`,

  previewDocument(file: File) {
    const form = new FormData()
    form.set('file', file)
    return fetch('/api/documents/preview', { method: 'POST', body: form }).then((r) => json<DocumentPreview>(r))
  },

  create(prompt: string, options: LessonOptions, file: File | null) {
    const form = new FormData()
    form.set('prompt', prompt)
    form.set('audience', options.audience)
    form.set('length', options.length)
    form.set('language', options.language)
    form.set('voice', options.voice)
    form.set('quality', options.quality)
    if (file) form.set('file', file)
    return fetch('/api/lessons', { method: 'POST', body: form }).then((r) =>
      json<{ id: string; mode: string; showcase: string | null }>(r),
    )
  },

  events(id: string): EventSource {
    return new EventSource(`/api/lessons/${id}/events`)
  },
}
