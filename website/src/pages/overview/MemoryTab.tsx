import { useState, useEffect, useCallback, useMemo, useRef, type ReactNode } from 'react'
import { XCircle, AlertTriangle, CheckCircle, RefreshCw, Hourglass, Check, BookOpen } from 'lucide-react'
import { api } from '../../api/client'
import { Card, CardTitle, Btn, SendBtn, Input, Badge, EmptyState } from '../../components/ui'
import InfoTip from '../../components/InfoTip'
import ErrorNotice from '../../components/ErrorNotice'
import SimpleSelect from '../../components/SimpleSelect'
import { esc } from '../../api/helpers'
import VectorMemoryCard from './VectorMemoryCard'
import EmbeddingModelCard from './EmbeddingModelCard'
import type { Lesson, SessionInfo } from '../../types'
import { useSortableTable } from '../../hooks/useSortableTable'
import SortableHeader from '../../components/SortableHeader'

import { i18nT } from '../../i18n/t'
import { fmtDateTimeNumeric } from '../../i18n/format'
export default function MemoryTab({ refreshTrigger }: { refreshTrigger: number }) {
  const [pref, setPref] = useState(''); const [proj, setProj] = useState(''); const [hist, setHist] = useState('')
  const [prefSaved, setPrefSaved] = useState(false); const [projSaved, setProjSaved] = useState(false); const [histSaved, setHistSaved] = useState(false)
  const [lessons, setLessons] = useState<Lesson[]>([]); const [rule, setRule] = useState(''); const [cat, setCat] = useState('knowledge'); const [scope, setScope] = useState('')
  const [lessonError, setLessonError] = useState<string | null>(null)
  const [savingLesson, setSavingLesson] = useState(false)
  const [lessonFeedback, setLessonFeedback] = useState<{
    tone: 'info' | 'warning' | 'error'
    text: string
  } | null>(null)
  const [idleHours, setIdleHours] = useState(3); const [maxDays, setMaxDays] = useState(90); const [settingsSaved, setSettingsSaved] = useState(false)
  const [migrated, setMigrated] = useState(false)
  const [vectorActive, setVectorActive] = useState(false)
  const [consolidating, setConsolidating] = useState(false)
  const [consolidateMsg, setConsolidateMsg] = useState<ReactNode>('')
  const [consolidateOk, setConsolidateOk] = useState(false)
  // Track all "Saved" / "consolidate-msg-clear" timeout ids so they can be
  // cleared on unmount — otherwise a pending setTimeout fires after the
  // component is gone and (in vitest) shows up as an unhandled error from
  // "tasks running past test environment teardown".
  const timeoutsRef = useRef<ReturnType<typeof setTimeout>[]>([])
  useEffect(() => () => {
    timeoutsRef.current.forEach(clearTimeout)
    timeoutsRef.current = []
  }, [])
  const scheduleClear = useCallback((fn: () => void, ms: number) => {
    const id = setTimeout(() => {
      timeoutsRef.current = timeoutsRef.current.filter(t => t !== id)
      fn()
    }, ms)
    timeoutsRef.current.push(id)
  }, [])
  const loadLessons = useCallback(async () => { const d = await api.lessons(); setLessons(d.lessons || []) }, [])
  const loadMemory = useCallback(() => {
    api.memoryPreferences().then(d => setPref(d.content || ''))
    api.memoryProjects().then(d => setProj(d.content || ''))
    api.memoryHistory().then(d => setHist(d.content || ''))
  }, [])
  const lessonComparators = useMemo(() => ({
    rule: (a: Lesson, b: Lesson) => a.rule.localeCompare(b.rule),
    category: (a: Lesson, b: Lesson) => a.category.localeCompare(b.category),
    ts: (a: Lesson, b: Lesson) => new Date(a.ts).getTime() - new Date(b.ts).getTime(),
  }), [])
  const recentLessons = useMemo(() => lessons.slice(-20), [lessons])
  const { sorted: sortedLessons, sort: lessonSort, toggle: toggleLessonSort } = useSortableTable(recentLessons, 'memory-lessons', lessonComparators, { key: 'ts', dir: 'desc' })
  useEffect(() => {
    loadMemory()
    api.memorySettings().then(d => { setIdleHours(d.history_idle_hours ?? 3); setMaxDays(d.history_max_days ?? 90); setMigrated(d.migrated ?? false) })
    loadLessons()
  }, [loadLessons, loadMemory])
  useEffect(() => { loadLessons(); loadMemory() }, [refreshTrigger, loadLessons, loadMemory])
  const consolidate = async () => {
    setConsolidating(true); setConsolidateMsg(''); setConsolidateOk(false)
    const sessions = await api.sessions(200).catch(() => ({ sessions: [] }))
    const keys = sessions?.sessions?.map((s: SessionInfo) => s.key).filter(Boolean) || []
    if (keys.length === 0) { setConsolidateMsg(<><XCircle className="lucide-inline" /> {i18nT('pages.overview.memoryTab.no_sessions_to_consolidate_start_a_chat_first')}</>); setConsolidating(false); return }
    const results = await Promise.allSettled(keys.map((k: string) => api.consolidateMemory(k, true)))
    const succeeded = results.filter(r => r.status === 'fulfilled').length
    const failed = results.filter(r => r.status === 'rejected').length
    if (failed > 0) setConsolidateMsg(<><AlertTriangle className="lucide-inline" /> {i18nT('pages.overview.memoryTab.consolidated_sessions_failed', { succeeded, total: keys.length, failed })}</>)
    else { setConsolidateMsg(<><CheckCircle className="lucide-inline" /> {i18nT('pages.overview.memoryTab.consolidated')} {i18nT('pages.overview.memoryTab.session', { count: succeeded })}</>); setConsolidateOk(true) }
    setConsolidating(false)
    scheduleClear(() => setConsolidateMsg(''), 4000)
  }
  const addLesson = async () => {
    if (!rule || savingLesson) return
    setLessonFeedback(null)
    setLessonError(null)
    // Hold the row disabled for the whole in-flight save. Without this, the
    // rule/scope boxes stay editable during the await, and the setRule('') /
    // setScope('') that runs on completion would erase a value the user typed
    // WHILE the request was in flight — an ordinary async race. Disabling the
    // inputs and the button for the await window closes that race at the source
    // for both fields, rather than diffing submitted-vs-current per field.
    setSavingLesson(true)
    // A blank scope box sends no repo_scope, so the lesson applies everywhere —
    // the store's default and the behaviour before this box existed. Only a
    // filled box narrows the lesson to one repository.
    let result
    try {
      result = await api.createLesson(rule, cat, scope.trim() || undefined)
    } catch (e) {
      // A malformed scope (e.g. "." or a leading slash) fails the backend's
      // SCOPE_FRAGMENT_RE, so /api/lessons returns HTTP 400 and the client's
      // j() throws an ApiError. Without this catch, Add would silently do
      // nothing and leave the inputs uncleared. Surface it through ErrorNotice
      // (the one error surface, per errors-use-error-notice), which recovers
      // the endpoint/status/code from the error journal by message. The rule
      // and scope drafts are deliberately LEFT in their boxes so the user can
      // correct the scope and retry — nothing was saved.
      setLessonError(e instanceof Error ? e.message : String(e))
      return
    } finally {
      setSavingLesson(false)
    }
    if (result.outcome === 'inserted' || result.outcome === 'enriched') {
      setRule('')
      setScope('')
      await loadLessons()
      return
    }
    if (result.outcome === 'unchanged') {
      // 'unchanged' means the submission completed against an already-stored
      // lesson and nothing needs retrying, so clear BOTH drafts — matching the
      // inserted/enriched branch. Clearing only rule would leave a stale repo
      // scope in the box that the next, unrelated Add would silently inherit.
      setRule('')
      setScope('')
      setLessonFeedback({
        tone: 'info',
        text: i18nT('pages.overview.memoryTab.lesson_already_stored'),
      })
      return
    }
    if (result.outcome === 'deduped') {
      setLessonFeedback({
        tone: 'warning',
        text: i18nT('pages.overview.memoryTab.lesson_already_covered', {
          reason: result.reason,
        }),
      })
      return
    }
    setLessonFeedback({
      tone: 'error',
      text: i18nT('pages.overview.memoryTab.lesson_not_saved', {
        reason: result.reason,
      }),
    })
  }
  // Delete is a destructive request that can fail (session gone, storage error,
  // an ok:false from the route). Without surfacing that, the row would appear
  // to stay for no reason. Route both a thrown ApiError and an ok:false through
  // ErrorNotice (the one error surface, per errors-use-error-notice); only
  // re-read the list when a delete actually happened.
  const deleteLessonRow = async (rule: string, repoScope: string | null) => {
    setLessonError(null)
    let res: { ok?: boolean } | null = null
    try {
      res = await api.deleteLesson(rule, repoScope)
    } catch (e) {
      setLessonError(e instanceof Error ? e.message : String(e))
      return
    }
    if (res && res.ok === false) {
      setLessonError(i18nT('pages.overview.memoryTab.lesson_not_deleted'))
      return
    }
    loadLessons()
  }
  return (<>
    {/* Graph/vector internals live on the Developer page (Memory tab); this
        surface is the user-facing browser: settings, preferences, projects,
        daily history, and lessons. */}
    <Card><CardTitle>{i18nT('pages.overview.memoryTab.memory_settings')} <InfoTip text={i18nT('pages.overview.memoryTab.controls_how_conversation_history_is_consolidate')} /></CardTitle>
      <div className="flex gap-3 items-end flex-wrap">
        <label htmlFor="memory-idle-hours" className="flex flex-col gap-1 text-[13px] text-muted">
          <span>{i18nT('pages.overview.memoryTab.consolidation_idle_hours')}</span>
          <input id="memory-idle-hours" aria-label={i18nT('pages.overview.memoryTab.consolidation_idle_hours')} type="number" min={0.5} max={24} step={0.5} className="w-24 bg-bg-elevated border border-border rounded-md px-3 py-2 text-text text-sm font-body outline-none transition-colors focus-ring" value={idleHours} onChange={e => setIdleHours(Number(e.target.value))} />
        </label>
        {!migrated && (
          <label htmlFor="memory-max-days" className="flex flex-col gap-1 text-[13px] text-muted">
            <span>{i18nT('pages.overview.memoryTab.history_retention_days')}</span>
            <input id="memory-max-days" aria-label={i18nT('pages.overview.memoryTab.history_retention_days')} type="number" min={7} max={365} step={1} className="w-24 bg-bg-elevated border border-border rounded-md px-3 py-2 text-text text-sm font-body outline-none transition-colors focus-ring" value={maxDays} onChange={e => setMaxDays(Number(e.target.value))} />
          </label>
        )}
        <Btn onClick={async () => { await api.saveMemorySettings({ history_idle_hours: idleHours, history_max_days: maxDays }); setSettingsSaved(true); scheduleClear(() => setSettingsSaved(false), 2000) }}>{settingsSaved ? <><Check className="lucide-inline" /> {i18nT('pages.overview.memoryTab.saved')}</> : i18nT('pages.overview.memoryTab.save')}</Btn>
        <Btn onClick={consolidate} disabled={consolidating}>{consolidating ? <><Hourglass className="lucide-inline" /> {i18nT('pages.overview.memoryTab.running')}</> : <><RefreshCw className="lucide-inline" /> {i18nT('pages.overview.memoryTab.summarize_now')}</>}</Btn>
        {consolidateMsg && <span className={`text-[13px] ${consolidateOk ? 'text-ok' : 'text-danger'}`}>{consolidateMsg}</span>}

        {migrated && <span className="text-[12px] text-muted ml-2">{i18nT('pages.overview.memoryTab.semantic_memory_active_text_files_are_read_only')}</span>}
      </div>
    </Card>
    <VectorMemoryCard onActiveChange={setVectorActive} onMigratedChange={setMigrated} />
    <EmbeddingModelCard />
    {!vectorActive && (<>
      <Card><CardTitle>{i18nT('pages.overview.memoryTab.preferences')} <InfoTip text={i18nT('pages.overview.memoryTab.learned_user_preferences_coding_style_tools_work')} /> <Btn onClick={async () => { await api.saveMemoryPreferences(pref); setPrefSaved(true); scheduleClear(() => setPrefSaved(false), 2000) }}>{prefSaved ? <><Check className="lucide-inline" /> {i18nT('pages.overview.memoryTab.saved')}</> : i18nT('pages.overview.memoryTab.save')}</Btn></CardTitle>
        <textarea aria-label={i18nT('pages.overview.memoryTab.preferences')} className="w-full bg-bg-elevated border border-border rounded-md p-3 text-text text-sm font-body outline-none resize-y leading-relaxed transition-colors focus-ring" rows={8} value={pref} onChange={e => setPref(e.target.value)} placeholder={i18nT('pages.overview.memoryTab.loading')} /></Card>
      <Card><CardTitle>{i18nT('pages.overview.memoryTab.projects')} <Btn onClick={async () => { await api.saveMemoryProjects(proj); setProjSaved(true); scheduleClear(() => setProjSaved(false), 2000) }}>{projSaved ? <><Check className="lucide-inline" /> {i18nT('pages.overview.memoryTab.saved')}</> : i18nT('pages.overview.memoryTab.save')}</Btn></CardTitle>
        <textarea aria-label={i18nT('pages.overview.memoryTab.projects')} className="w-full bg-bg-elevated border border-border rounded-md p-3 text-text text-sm font-body outline-none resize-y leading-relaxed transition-colors focus-ring" rows={8} value={proj} onChange={e => setProj(e.target.value)} placeholder={i18nT('pages.overview.memoryTab.loading')} /></Card>
      <Card><CardTitle>{i18nT('pages.overview.memoryTab.daily_history')} <Btn onClick={async () => { await api.saveMemoryHistory(hist); setHistSaved(true); scheduleClear(() => setHistSaved(false), 2000) }}>{histSaved ? <><Check className="lucide-inline" /> {i18nT('pages.overview.memoryTab.saved')}</> : i18nT('pages.overview.memoryTab.save')}</Btn></CardTitle>
        <textarea aria-label={i18nT('pages.overview.memoryTab.daily_history')} className="w-full bg-bg-elevated border border-border rounded-md p-3 text-text text-sm font-mono outline-none resize-y leading-relaxed transition-colors focus-ring" rows={10} value={hist} onChange={e => setHist(e.target.value)} placeholder={i18nT('pages.overview.memoryTab.no_history_yet')} /></Card>
    </>)}
    {!vectorActive && (
      <Card><CardTitle>{i18nT('pages.overview.memoryTab.lessons')} <InfoTip text={i18nT('pages.overview.memoryTab.persistent_lessons_injected_into_every_session_a')} /></CardTitle>
      <div className="flex gap-2 items-center flex-wrap mb-3">
        <Input placeholder={i18nT('pages.overview.memoryTab.rule_e_g_always_use_tabs_not_spaces')} style={{ flex: 2 }} value={rule} onChange={e => setRule(e.target.value)} disabled={savingLesson} />
        <SimpleSelect
          aria-label={i18nT('pages.overview.memoryTab.category')}
          style={{ flex: '0 0 140px' }}
          options={['knowledge', 'tool', 'preference']}
          optionLabels={[i18nT('pages.overview.memoryTab.knowledge'), i18nT('pages.overview.memoryTab.tool'), i18nT('pages.overview.memoryTab.preference')]}
          value={cat}
          onChange={setCat}
        />
        <Input aria-label={i18nT('pages.overview.memoryTab.repo_scope_label')} placeholder={i18nT('pages.overview.memoryTab.repo_scope_placeholder')} style={{ flex: '0 0 200px' }} value={scope} onChange={e => setScope(e.target.value)} disabled={savingLesson} />
        <SendBtn onClick={addLesson} disabled={savingLesson}>{i18nT('pages.overview.memoryTab.add')}</SendBtn>
        {lessonFeedback && (
          <span
            role={lessonFeedback.tone === 'error' ? 'alert' : 'status'}
            className={`text-[13px] ${
              lessonFeedback.tone === 'error'
                ? 'text-danger'
                : lessonFeedback.tone === 'warning'
                  ? 'text-warn'
                  : 'text-muted'
            }`}
          >
            {lessonFeedback.text}
          </span>
        )}
        {/* No hand-off: askAgent stays OFF on this notice because the hand-off
            navigates to the chat and unmounts this row, which would destroy the
            unsaved add-lesson drafts still in the box -- the `rule` and repo
            `scope` inputs. It surfaces both a caught add failure (an invalid
            scope rejected 400) and a caught delete failure; in the add case the
            drafts are live and unsaved, so the no-hand-off form applies. Renders
            through ErrorNotice (the one error surface), inline to sit in this
            flex row. */}
        <ErrorNotice message={lessonError} variant="inline" onDismiss={() => setLessonError(null)} />
      </div>
      <table className="w-full border-collapse table-striped"><thead><tr><SortableHeader label={i18nT('pages.overview.memoryTab.rule')} sortKey="rule" sort={lessonSort} onToggle={toggleLessonSort} /><SortableHeader label={i18nT('pages.overview.memoryTab.category')} sortKey="category" sort={lessonSort} onToggle={toggleLessonSort} /><SortableHeader label={i18nT('pages.overview.memoryTab.when')} sortKey="ts" sort={lessonSort} onToggle={toggleLessonSort} /><th aria-label={i18nT('pages.overview.memoryTab.actions')} className="text-left text-muted text-[12px] uppercase tracking-[.04em] px-2.5 py-2 border-b border-border font-medium"></th></tr></thead>
        <tbody>{lessons.length === 0 ? <tr><td colSpan={4}><EmptyState icon={<BookOpen className="lucide-inline" />} title={i18nT('pages.overview.memoryTab.no_lessons_yet')} subtitle={i18nT('pages.overview.memoryTab.lessons_empty_subtitle')} /></td></tr> : sortedLessons.map((l) => (
          <tr key={`${l.rule}-${l.repo_scope ?? ''}-${l.ts}`} className="hover:bg-bg-hover transition-colors"><td className="px-2.5 py-2 border-b border-border text-sm">{esc(l.rule)}{l.repo_scope ? <Badge variant="muted" className="ml-2">{esc(l.repo_scope)}</Badge> : null}</td><td className="px-2.5 py-2 border-b border-border text-sm"><Badge variant="ok">{l.category}</Badge></td><td className="px-2.5 py-2 border-b border-border text-sm">{fmtDateTimeNumeric(l.ts)}</td>
            <td className="px-2.5 py-2 border-b border-border text-sm"><Btn danger onClick={() => deleteLessonRow(l.rule, l.repo_scope ?? null)}>{i18nT('pages.overview.memoryTab.delete')}</Btn></td></tr>
        ))}</tbody></table></Card>
    )}
  </>)
}
