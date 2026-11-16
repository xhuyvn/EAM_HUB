import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { AgentInventoryView } from './components/AgentInventoryView'
import { AttentionQueueView } from './components/AttentionQueueView'
import { ProjectAssessmentView } from './components/ProjectAssessmentView'
import { RunInspectorView } from './components/RunInspectorView'
import { TasksRunsView } from './components/TasksRunsView'
import { runSseLoop } from './sseClient'
import { applyHudSseEvent, buildAttentionQueue, formatTimestampLocal } from './state'
import type {
  AgentInventoryResponse,
  AssessmentDetail,
  AssessmentDiff,
  AssessmentListItem,
  ConnectionState,
  HudProjection,
  RunInspectorData,
  SourceScheduleStatus,
} from './types'

type HudTab = 'attention' | 'tasks' | 'inspector' | 'agents' | 'ppm'

const TABS: Array<{ id: HudTab; label: string }> = [
  { id: 'attention', label: 'Attention Queue' },
  { id: 'tasks', label: 'Tasks & Runs' },
  { id: 'inspector', label: 'Run Inspector' },
  { id: 'agents', label: 'Agent Inventory' },
  { id: 'ppm', label: 'Project Assessment' },
]

export default function App() {
  const [tokenInput, setTokenInput] = useState('')
  const [token, setToken] = useState('')
  const [activeTab, setActiveTab] = useState<HudTab>('tasks')

  const [snapshot, setSnapshot] = useState<HudProjection | null>(null)
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')
  const [connectionDetail, setConnectionDetail] = useState<string>('')
  const [lastFrameAt, setLastFrameAt] = useState<number | null>(null)
  const [staleSince, setStaleSince] = useState<string | null>(null)
  const [globalError, setGlobalError] = useState<string | null>(null)

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [inspectorData, setInspectorData] = useState<RunInspectorData | null>(null)
  const [inspectorLoading, setInspectorLoading] = useState(false)
  const [inspectorError, setInspectorError] = useState<string | null>(null)

  const [agents, setAgents] = useState<AgentInventoryResponse | null>(null)
  const [agentsLoading, setAgentsLoading] = useState(false)
  const [agentsError, setAgentsError] = useState<string | null>(null)

  const [projectId, setProjectId] = useState('proj-d0-demo')
  const [assessments, setAssessments] = useState<AssessmentListItem[]>([])
  const [assessmentDetail, setAssessmentDetail] = useState<AssessmentDetail | null>(null)
  const [assessmentDiff, setAssessmentDiff] = useState<AssessmentDiff | null>(null)
  const [ppmLoading, setPpmLoading] = useState(false)
  const [ppmError, setPpmError] = useState<string | null>(null)

  const [sources, setSources] = useState<SourceScheduleStatus[]>([])

  const sseAbortRef = useRef<AbortController | null>(null)
  const selectedRunRef = useRef<string | null>(null)
  selectedRunRef.current = selectedRunId

  const authedFetch = useCallback(
    async (url: string, init?: RequestInit): Promise<Response> => {
      const headers = new Headers(init?.headers)
      headers.set('Authorization', `Bearer ${token}`)
      return fetch(url, { ...init, headers })
    },
    [token],
  )

  const loadAgents = useCallback(async () => {
    if (!token) return
    setAgentsLoading(true)
    setAgentsError(null)
    try {
      const res = await authedFetch('/v1/agents')
      if (!res.ok) throw new Error(`GET /v1/agents HTTP ${res.status}`)
      setAgents((await res.json()) as AgentInventoryResponse)
    } catch (err) {
      setAgentsError(err instanceof Error ? err.message : 'Failed to load agents')
    } finally {
      setAgentsLoading(false)
    }
  }, [token, authedFetch])

  const loadSources = useCallback(async () => {
    if (!token) return
    try {
      const res = await authedFetch('/v1/ppm/sources')
      if (!res.ok) return
      const list = (await res.json()) as Array<{ source_id: string; canonical_root?: string }>
      const statuses = await Promise.all(
        list.map(async (src) => {
          try {
            const schedRes = await authedFetch(`/v1/ppm/sources/${encodeURIComponent(src.source_id)}/schedule`)
            if (!schedRes.ok) return { source_id: src.source_id, canonical_root: src.canonical_root }
            return (await schedRes.json()) as SourceScheduleStatus
          } catch {
            return { source_id: src.source_id, canonical_root: src.canonical_root }
          }
        }),
      )
      setSources(statuses)
    } catch {
      // Optional scheduler observability
    }
  }, [token, authedFetch])

  const loadAssessmentDetail = useCallback(
    async (assessmentId: string, allItems: AssessmentListItem[]) => {
      if (!token) return
      setPpmLoading(true)
      setPpmError(null)
      try {
        const res = await authedFetch(`/v1/ppm/assessments/${encodeURIComponent(assessmentId)}`)
        if (!res.ok) throw new Error(`GET /v1/ppm/assessments/${assessmentId} HTTP ${res.status}`)
        const detail = (await res.json()) as AssessmentDetail
        setAssessmentDetail(detail)

        const idx = allItems.findIndex((a) => a.id === assessmentId)
        const prevItem = idx >= 0 ? allItems[idx + 1] : undefined
        if (prevItem) {
          const diffRes = await authedFetch(
            `/v1/ppm/assessments/${encodeURIComponent(assessmentId)}/diff?against=${encodeURIComponent(prevItem.id)}`,
          )
          if (diffRes.ok) {
            setAssessmentDiff((await diffRes.json()) as AssessmentDiff)
          } else {
            setAssessmentDiff(null)
          }
        } else {
          setAssessmentDiff(null)
        }
      } catch (err) {
        setPpmError(err instanceof Error ? err.message : 'Failed to load assessment detail')
      } finally {
        setPpmLoading(false)
      }
    },
    [token, authedFetch],
  )

  const loadProjectAssessments = useCallback(
    async (targetProjectId: string) => {
      if (!token || !targetProjectId) return
      setPpmLoading(true)
      setPpmError(null)
      try {
        const res = await authedFetch(
          `/v1/ppm/projects/${encodeURIComponent(targetProjectId)}/assessments`,
        )
        if (!res.ok) throw new Error(`GET assessments HTTP ${res.status}`)
        const list = (await res.json()) as AssessmentListItem[]
        setAssessments(list)
        const current = list.find((a) => a.is_current) || list[0]
        if (current) {
          await loadAssessmentDetail(current.id, list)
        } else {
          setAssessmentDetail(null)
          setAssessmentDiff(null)
        }
      } catch (err) {
        setPpmError(err instanceof Error ? err.message : 'Failed to load project assessments')
      } finally {
        setPpmLoading(false)
      }
    },
    [token, authedFetch, loadAssessmentDetail],
  )

  const loadRunInspector = useCallback(
    async (runId: string) => {
      if (!token) return
      setSelectedRunId(runId)
      setActiveTab('inspector')
      setInspectorLoading(true)
      setInspectorError(null)
      try {
        const res = await authedFetch(`/v1/hud/runs/${encodeURIComponent(runId)}/inspector`)
        if (!res.ok) throw new Error(`GET run inspector HTTP ${res.status}`)
        setInspectorData((await res.json()) as RunInspectorData)
      } catch (err) {
        setInspectorError(err instanceof Error ? err.message : 'Failed to load run inspector')
        setInspectorData(null)
      } finally {
        setInspectorLoading(false)
      }
    },
    [token, authedFetch],
  )

  const startSnapshotAndStream = useCallback(
    async (activeToken: string) => {
      sseAbortRef.current?.abort()
      const controller = new AbortController()
      sseAbortRef.current = controller

      setGlobalError(null)
      setConnectionState('connecting')
      setConnectionDetail('')

      try {
        const snapRes = await fetch('/v1/hud/snapshot', {
          headers: { Authorization: `Bearer ${activeToken}` },
          signal: controller.signal,
        })
        if (snapRes.status === 401 || snapRes.status === 403) {
          setConnectionState('disconnected')
          setGlobalError(`Authentication/authorization failed (HTTP ${snapRes.status})`)
          return
        }
        if (!snapRes.ok) {
          throw new Error(`Snapshot failed: HTTP ${snapRes.status}`)
        }
        const snap = (await snapRes.json()) as HudProjection
        setSnapshot(snap)
        setLastFrameAt(Date.now())
        setStaleSince(null)

        const firstProject = snap.rows.find((r) => r.project?.project_id)?.project?.project_id
        if (firstProject) {
          setProjectId(firstProject)
        }

        void runSseLoop({
          token: activeToken,
          initialCursor: snap.snapshot_cursor,
          signal: controller.signal,
          onStateChange: (state, detail) => {
            setConnectionState(state)
            setConnectionDetail(detail || '')
            if (state === 'live') {
              setLastFrameAt(Date.now())
              setStaleSince(null)
            }
          },
          onHeartbeat: () => {
            setLastFrameAt(Date.now())
            setStaleSince(null)
          },
          onEvent: (eventName, data, eventId) => {
            setLastFrameAt(Date.now())
            setStaleSince(null)
            if (eventName === 'row.removed') {
              const removedRunId = typeof data.run_id === 'string' ? data.run_id : null
              if (removedRunId && selectedRunRef.current === removedRunId) {
                setSelectedRunId(null)
                setInspectorData(null)
              }
            }
            setSnapshot((prev) => (prev ? applyHudSseEvent(prev, eventName, data, eventId) : prev))
          },
          onResyncRequired: () => {
            setConnectionState('resyncing')
            void startSnapshotAndStream(activeToken)
          },
          onAuthError: (status) => {
            setConnectionState('disconnected')
            setGlobalError(`SSE authentication error: HTTP ${status}`)
          },
        })
      } catch (err) {
        if (controller.signal.aborted) return
        setConnectionState('disconnected')
        setGlobalError(err instanceof Error ? err.message : 'Failed to connect')
      }
    },
    [],
  )

  useEffect(() => {
    if (!token) return
    void startSnapshotAndStream(token)
    void loadAgents()
    void loadSources()
    void loadProjectAssessments(projectId)
    return () => {
      sseAbortRef.current?.abort()
    }
  }, [token]) // eslint-disable-line react-hooks/exhaustive-deps

  // Track stale SSE connection (>30s without heartbeat/event)
  useEffect(() => {
    if (connectionState !== 'live' || !lastFrameAt) return
    const timer = setInterval(() => {
      if (Date.now() - lastFrameAt > 30_000) {
        setConnectionState('stale')
        setStaleSince((prev) => prev || new Date().toISOString())
      }
    }, 5000)
    return () => clearInterval(timer)
  }, [connectionState, lastFrameAt])

  function handleConnect(e: FormEvent) {
    e.preventDefault()
    const trimmed = tokenInput.trim()
    if (!trimmed) return
    setToken(trimmed)
  }

  function handleDisconnect() {
    sseAbortRef.current?.abort()
    setToken('')
    setSnapshot(null)
    setAgents(null)
    setAssessments([])
    setAssessmentDetail(null)
    setAssessmentDiff(null)
    setInspectorData(null)
    setSelectedRunId(null)
    setConnectionState('disconnected')
  }

  async function handleCancelRun(runId: string) {
    const res = await authedFetch(`/v1/runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
    })
    if (!res.ok) {
      throw new Error(`Cancel failed: HTTP ${res.status}`)
    }
  }

  async function handleRetryRun(runId: string) {
    const res = await authedFetch(`/v1/runs/${encodeURIComponent(runId)}/retry`, {
      method: 'POST',
    })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(`Retry rejected (HTTP ${res.status}): ${text}`)
    }
  }

  const knownProjectIds = useMemo(() => {
    const set = new Set<string>()
    if (projectId) set.add(projectId)
    for (const r of snapshot?.rows || []) {
      if (r.project?.project_id) set.add(r.project.project_id)
    }
    return Array.from(set)
  }, [snapshot, projectId])

  const attentionItems = useMemo(
    () => buildAttentionQueue(snapshot?.rows || [], assessmentDetail, sources),
    [snapshot, assessmentDetail, sources],
  )

  function handleTabKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const idx = TABS.findIndex((t) => t.id === activeTab)
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      setActiveTab(TABS[(idx + 1) % TABS.length].id)
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      setActiveTab(TABS[(idx - 1 + TABS.length) % TABS.length].id)
    }
  }

  const connLabel =
    connectionState === 'stale' && staleSince
      ? `stale since ${formatTimestampLocal(staleSince)}`
      : connectionDetail
        ? `${connectionState} (${connectionDetail})`
        : connectionState

  return (
    <div className="hud-shell">
      <header className="hud-topbar">
        <div>
          <h1>EAP Operator HUD</h1>
          <div className="muted small">
            {snapshot
              ? `Revision ${snapshot.projection_revision} · As of ${formatTimestampLocal(snapshot.as_of)} · Scope ${snapshot.viewer_scope_ref}`
              : 'Not connected — enter bearer token (kept in memory only)'}
          </div>
        </div>

        <div className="topbar-controls">
          <span
            className={`badge ${
              connectionState === 'live'
                ? 'tone-ok'
                : connectionState === 'reconnecting' || connectionState === 'resyncing' || connectionState === 'stale'
                  ? 'tone-warn'
                  : 'tone-neutral'
            }`}
            data-testid="connection-status"
          >
            {connectionState === 'live' ? '●' : connectionState === 'disconnected' ? '○' : '⚠'}{' '}
            {connLabel}
          </span>

          {!token ? (
            <form onSubmit={handleConnect} className="btn-group">
              <input
                type="password"
                aria-label="Operator Bearer Token"
                placeholder="Bearer token (memory only)"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
              />
              <button type="submit" className="btn-primary">
                Connect
              </button>
            </form>
          ) : (
            <div className="btn-group">
              <button
                type="button"
                className="btn-sm"
                onClick={() => void startSnapshotAndStream(token)}
              >
                Resync Snapshot
              </button>
              <button type="button" className="btn-sm" onClick={handleDisconnect}>
                Disconnect
              </button>
            </div>
          )}
        </div>
      </header>

      {globalError && (
        <div role="alert" className="banner bad">
          {globalError}
        </div>
      )}

      <nav
        role="tablist"
        aria-label="HUD Views"
        className="hud-tabs"
        onKeyDown={handleTabKeyDown}
      >
        {TABS.map((tab) => {
          const isSelected = activeTab === tab.id
          return (
            <button
              key={tab.id}
              role="tab"
              type="button"
              aria-selected={isSelected}
              className={`tab-btn ${isSelected ? 'tab-active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
              {tab.id === 'attention' && attentionItems.length > 0 && (
                <span className="pill-count">{attentionItems.length}</span>
              )}
            </button>
          )
        })}
      </nav>

      <main className="hud-content">
        {activeTab === 'attention' && (
          <AttentionQueueView
            items={attentionItems}
            onOpenRun={(runId) => void loadRunInspector(runId)}
            onOpenProject={(pid) => {
              if (pid) {
                setProjectId(pid)
                void loadProjectAssessments(pid)
              }
              setActiveTab('ppm')
            }}
          />
        )}

        {activeTab === 'tasks' && (
          <TasksRunsView
            rows={snapshot?.rows || []}
            selectedRunId={selectedRunId}
            onInspectRun={(runId) => void loadRunInspector(runId)}
            onSelectProject={(pid) => {
              setProjectId(pid)
              void loadProjectAssessments(pid)
              setActiveTab('ppm')
            }}
            onCancelRun={handleCancelRun}
            onRetryRun={handleRetryRun}
          />
        )}

        {activeTab === 'inspector' && (
          <RunInspectorView
            data={inspectorData}
            loading={inspectorLoading}
            error={inspectorError}
            token={token}
            onBack={() => setActiveTab('tasks')}
          />
        )}

        {activeTab === 'agents' && (
          <AgentInventoryView
            inventory={agents}
            loading={agentsLoading}
            error={agentsError}
            token={token}
            onRefresh={() => void loadAgents()}
          />
        )}

        {activeTab === 'ppm' && (
          <ProjectAssessmentView
            projectId={projectId}
            knownProjectIds={knownProjectIds}
            assessments={assessments}
            detail={assessmentDetail}
            diff={assessmentDiff}
            loading={ppmLoading}
            error={ppmError}
            token={token}
            onChangeProject={(pid) => {
              setProjectId(pid)
              void loadProjectAssessments(pid)
            }}
            onSelectAssessment={(id) => void loadAssessmentDetail(id, assessments)}
            onRefresh={() => void loadProjectAssessments(projectId)}
          />
        )}
      </main>
    </div>
  )
}
