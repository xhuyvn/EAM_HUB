import type { ConnectionState } from './types'

export interface SseClientOptions {
  token: string
  initialCursor: string
  projectId?: string | null
  onEvent: (eventName: string, data: Record<string, unknown>, eventId: string | null) => void
  onHeartbeat: () => void
  onResyncRequired: (reason: string) => void
  onStateChange: (state: ConnectionState, detail?: string) => void
  onAuthError: (status: number) => void
  signal: AbortSignal
  baseDelayMs?: number
  maxDelayMs?: number
}

export function parseSseBlock(block: string): {
  id: string | null
  event: string
  data: string
  isComment: boolean
} | null {
  const lines = block.split(/\r?\n/)
  let id: string | null = null
  let event = 'message'
  const dataLines: string[] = []
  let sawField = false
  let sawComment = false

  for (const line of lines) {
    if (!line) continue
    if (line.startsWith(':')) {
      sawComment = true
      continue
    }
    sawField = true
    const colonIdx = line.indexOf(':')
    const field = colonIdx >= 0 ? line.slice(0, colonIdx) : line
    let value = colonIdx >= 0 ? line.slice(colonIdx + 1) : ''
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'id') {
      id = value
    } else if (field === 'event') {
      event = value
    } else if (field === 'data') {
      dataLines.push(value)
    }
  }

  if (!sawField && sawComment) {
    return { id: null, event: 'heartbeat', data: '', isComment: true }
  }
  if (!sawField) return null
  return { id, event, data: dataLines.join('\n'), isComment: false }
}

export async function runSseLoop(options: SseClientOptions): Promise<void> {
  const {
    token,
    initialCursor,
    projectId,
    onEvent,
    onHeartbeat,
    onResyncRequired,
    onStateChange,
    onAuthError,
    signal,
    baseDelayMs = 500,
    maxDelayMs = 10000,
  } = options

  let currentCursor = initialCursor
  let attempt = 0
  let isReconnect = false

  while (!signal.aborted) {
    onStateChange(isReconnect ? 'reconnecting' : 'connecting', isReconnect ? `attempt ${attempt + 1}` : undefined)

    const params = new URLSearchParams()
    params.set('heartbeat_sec', '2')
    if (!isReconnect) {
      params.set('cursor', currentCursor)
    }
    if (projectId) {
      params.set('project_id', projectId)
    }

    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      Accept: 'text/event-stream',
    }
    if (isReconnect && currentCursor) {
      headers['Last-Event-ID'] = currentCursor
    }

    try {
      const url = `/v1/hud/events${params.toString() ? `?${params.toString()}` : ''}`
      const response = await fetch(url, { headers, signal })

      if (response.status === 401 || response.status === 403) {
        onAuthError(response.status)
        return
      }
      if (!response.ok || !response.body) {
        throw new Error(`SSE HTTP ${response.status}`)
      }

      onStateChange('live')
      attempt = 0

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (!signal.aborted) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split(/\r?\n\r?\n/)
        buffer = blocks.pop() ?? ''

        for (const block of blocks) {
          const parsed = parseSseBlock(block)
          if (!parsed) continue
          if (parsed.isComment) {
            onHeartbeat()
            continue
          }
          if (parsed.id) {
            currentCursor = parsed.id
          }
          let payload: Record<string, unknown> = {}
          if (parsed.data) {
            try {
              const raw = JSON.parse(parsed.data)
              if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
                payload = raw as Record<string, unknown>
              }
            } catch {
              payload = { raw: parsed.data }
            }
          }
          if (parsed.event === 'resync_required') {
            const reason = typeof payload.reason === 'string' ? payload.reason : 'resync_required'
            onResyncRequired(reason)
            return
          }
          onEvent(parsed.event, payload, parsed.id)
        }
      }
    } catch {
      if (signal.aborted) return
    }

    if (signal.aborted) return
    isReconnect = true
    attempt += 1
    const exp = Math.min(maxDelayMs, baseDelayMs * 2 ** Math.min(attempt - 1, 5))
    const jitter = Math.floor(Math.random() * Math.min(300, exp * 0.25))
    onStateChange('reconnecting', `retry #${attempt}`)
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, exp + jitter)
      signal.addEventListener(
        'abort',
        () => {
          clearTimeout(timer)
          resolve()
        },
        { once: true },
      )
    })
  }
}
