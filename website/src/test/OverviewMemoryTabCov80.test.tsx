import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

/**
 * Companion to integration/MemoryTab.integration.test.tsx (which drives the tab
 * against the MSW fixtures). This file stubs the api client directly so the
 * write paths — every Save, the lesson add/delete, and the manual consolidation
 * including its partial-failure branch — are assertable by their calls.
 */
const api = {
  lessons: vi.fn(),
  memoryPreferences: vi.fn(),
  memoryProjects: vi.fn(),
  memoryHistory: vi.fn(),
  memorySettings: vi.fn(),
  saveMemorySettings: vi.fn(),
  saveMemoryPreferences: vi.fn(),
  saveMemoryProjects: vi.fn(),
  saveMemoryHistory: vi.fn(),
  createLesson: vi.fn(),
  deleteLesson: vi.fn(),
  sessions: vi.fn(),
  consolidateMemory: vi.fn(),
}
vi.mock('../api/client', () => ({ api }))
// Both cards own their own queries and their own tests; here they are seams that
// report the vector/migration state this tab branches on.
vi.mock('../pages/overview/VectorMemoryCard', () => ({ default: () => <div data-testid="vector-card" /> }))
vi.mock('../pages/overview/EmbeddingModelCard', () => ({ default: () => <div data-testid="embed-card" /> }))

const MemoryTab = (await import('../pages/overview/MemoryTab')).default

const LESSONS = [
  { rule: 'zzq-rule-beta', category: 'tool', ts: '2026-01-02T00:00:00Z' },
  { rule: 'zzq-rule-alpha', category: 'knowledge', ts: '2026-01-01T00:00:00Z' },
]

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.lessons.mockResolvedValue({ lessons: LESSONS })
  api.memoryPreferences.mockResolvedValue({ content: 'zzq-prefs-body' })
  api.memoryProjects.mockResolvedValue({ content: 'zzq-projects-body' })
  api.memoryHistory.mockResolvedValue({ content: 'zzq-history-body' })
  api.memorySettings.mockResolvedValue({
    history_idle_hours: 4, history_max_days: 30, migrated: false,
  })
  api.saveMemorySettings.mockResolvedValue({ ok: true })
  api.saveMemoryPreferences.mockResolvedValue({ ok: true })
  api.saveMemoryProjects.mockResolvedValue({ ok: true })
  api.saveMemoryHistory.mockResolvedValue({ ok: true })
  api.createLesson.mockResolvedValue({ ok: true, outcome: 'inserted', reason: '' })
  api.deleteLesson.mockResolvedValue({ ok: true })
  api.sessions.mockResolvedValue({ sessions: [{ key: 'zzq-s1' }, { key: 'zzq-s2' }] })
  api.consolidateMemory.mockResolvedValue({ ok: true })
})

afterEach(() => {
  vi.useRealTimers()
})

/** The Save button inside the card whose heading contains `heading`. */
function saveIn(heading: RegExp): HTMLButtonElement {
  const title = screen.getByText(heading)
  const card = title.closest('.card-glow') ?? title.closest('div')?.parentElement
  const button = Array.from((card as HTMLElement).querySelectorAll('button'))
    .find((b) => /save/i.test(b.textContent ?? ''))
  return button as HTMLButtonElement
}

describe('MemoryTab — settings', () => {
  it('loads the saved retention settings and writes both fields back', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    const inputs = await waitFor(() => {
      const found = screen.getAllByRole('spinbutton') as HTMLInputElement[]
      expect(found[0].value).toBe('4')
      return found
    })
    expect(inputs[1].value).toBe('30')

    fireEvent.change(inputs[0], { target: { value: '6' } })
    fireEvent.change(inputs[1], { target: { value: '45' } })
    await userEvent.click(saveIn(/Memory Settings/i))

    await waitFor(() => expect(api.saveMemorySettings).toHaveBeenCalledWith({
      history_idle_hours: 6, history_max_days: 45,
    }))
    expect(await screen.findByText(/Saved/)).toBeInTheDocument()
  })

  it('hides the retention field, and the text-file editors, once memory is migrated', async () => {
    api.memorySettings.mockResolvedValue({
      history_idle_hours: 3, history_max_days: 90, migrated: true,
    })
    render(<MemoryTab refreshTrigger={0} />)
    await waitFor(() => expect(screen.getAllByRole('spinbutton')).toHaveLength(1))
    expect(screen.getByText(/read-only/i)).toBeInTheDocument()
  })

  it('clears the transient Saved marker on its own timer', async () => {
    vi.useFakeTimers()
    render(<MemoryTab refreshTrigger={0} />)
    await act(async () => {})
    const save = saveIn(/Memory Settings/i)
    fireEvent.click(save)
    await act(async () => {})
    expect(save.textContent).toContain('Saved')

    await act(async () => { vi.advanceTimersByTime(2000) })
    expect(save.textContent).not.toContain('Saved')
  })
})

describe('MemoryTab — the three text stores', () => {
  it('loads each store and saves the edited text back to its own endpoint', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    const prefs = await screen.findByRole('textbox', { name: /preferences/i }) as HTMLTextAreaElement
    await waitFor(() => expect(prefs.value).toBe('zzq-prefs-body'))

    fireEvent.change(prefs, { target: { value: 'zzq-prefs-edited' } })
    await userEvent.click(saveIn(/^Preferences$/))
    await waitFor(() => expect(api.saveMemoryPreferences).toHaveBeenCalledWith('zzq-prefs-edited'))

    const projects = screen.getByRole('textbox', { name: /projects/i }) as HTMLTextAreaElement
    fireEvent.change(projects, { target: { value: 'zzq-projects-edited' } })
    await userEvent.click(saveIn(/^Projects$/))
    await waitFor(() => expect(api.saveMemoryProjects).toHaveBeenCalledWith('zzq-projects-edited'))

    const history = screen.getByRole('textbox', { name: /daily history/i }) as HTMLTextAreaElement
    fireEvent.change(history, { target: { value: 'zzq-history-edited' } })
    await userEvent.click(saveIn(/Daily History/i))
    await waitFor(() => expect(api.saveMemoryHistory).toHaveBeenCalledWith('zzq-history-edited'))
  })

  it('re-reads every store when the parent bumps the refresh trigger', async () => {
    const { rerender } = render(<MemoryTab refreshTrigger={0} />)
    await waitFor(() => expect(api.memoryPreferences).toHaveBeenCalled())
    const before = api.memoryPreferences.mock.calls.length
    const lessonsBefore = api.lessons.mock.calls.length
    rerender(<MemoryTab refreshTrigger={1} />)
    await waitFor(() =>
      expect(api.memoryPreferences.mock.calls.length).toBeGreaterThan(before))
    expect(api.lessons.mock.calls.length).toBeGreaterThan(lessonsBefore)
  })

  it('tolerates an empty payload rather than rendering undefined', async () => {
    api.memoryPreferences.mockResolvedValue({})
    render(<MemoryTab refreshTrigger={0} />)
    const prefs = await screen.findByRole('textbox', { name: /preferences/i }) as HTMLTextAreaElement
    await waitFor(() => expect(prefs.value).toBe(''))
  })
})

describe('MemoryTab — lessons', () => {
  it('lists the stored lessons, newest first by default', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const rules = Array.from(document.querySelectorAll('tbody tr td:first-child'))
      .map((td) => td.textContent)
    expect(rules).toEqual(['zzq-rule-beta', 'zzq-rule-alpha'])
  })

  it('re-sorts on a header click', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    await userEvent.click(screen.getByText(/^Rule$/))
    const rules = Array.from(document.querySelectorAll('tbody tr td:first-child'))
      .map((td) => td.textContent)
    expect(rules).toEqual(['zzq-rule-alpha', 'zzq-rule-beta'])

    await userEvent.click(screen.getByText(/^Category$/))
    const cats = Array.from(document.querySelectorAll('tbody tr td:nth-child(2)'))
      .map((td) => td.textContent)
    expect(cats).toEqual(['knowledge', 'tool'])
  })

  it('adds a lesson with the chosen category, then re-reads the list', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const reads = api.lessons.mock.calls.length
    const input = screen.getByPlaceholderText(/Rule/) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'zzq-new-rule' } })
    await userEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    await waitFor(() => expect(api.createLesson).toHaveBeenCalledWith('zzq-new-rule', 'knowledge', undefined))
    await waitFor(() => expect(api.lessons.mock.calls.length).toBeGreaterThan(reads))
    expect(input.value).toBe('')
  })

  it('forwards a filled repo scope and clears it after a successful add', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const input = screen.getByPlaceholderText(/Rule/) as HTMLInputElement
    const scopeInput = screen.getByPlaceholderText(/Repo scope/) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'zzq-scoped-rule' } })
    fireEvent.change(scopeInput, { target: { value: '  src/kiro_crew  ' } })
    await userEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    // The scope is trimmed before it is sent, and a blank box would send
    // undefined (no repo_scope) — the pre-affordance default.
    await waitFor(() => expect(api.createLesson).toHaveBeenCalledWith('zzq-scoped-rule', 'knowledge', 'src/kiro_crew'))
    await waitFor(() => expect(scopeInput.value).toBe(''))
    expect(input.value).toBe('')
  })

  it('surfaces a rejected scope through ErrorNotice and keeps the drafts', async () => {
    // An invalid scope makes /api/lessons return 400, so j() throws and
    // createLesson rejects. The catch must show the error and leave the rule
    // and scope in their boxes so the user can fix the scope and retry.
    api.createLesson.mockRejectedValue(new Error('scope must be a path fragment'))
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const reads = api.lessons.mock.calls.length
    const input = screen.getByPlaceholderText(/Rule/) as HTMLInputElement
    const scopeInput = screen.getByPlaceholderText(/Repo scope/) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'zzq-bad-scope-rule' } })
    fireEvent.change(scopeInput, { target: { value: '.' } })
    await userEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/scope must be a path fragment/i)
    // Nothing was saved, so the list is not re-read and the drafts survive.
    expect(api.lessons).toHaveBeenCalledTimes(reads)
    expect(input.value).toBe('zzq-bad-scope-rule')
    expect(scopeInput.value).toBe('.')
  })

  it('disables the row while a save is in flight so an edit cannot be erased', async () => {
    // Hold createLesson open to observe the in-flight window. While it is
    // pending, the rule/scope inputs and Add button are disabled, so a value
    // cannot be typed mid-flight and then erased by the completion's reset.
    let resolve!: (v: { ok: boolean; outcome: string; reason: string }) => void
    api.createLesson.mockReturnValue(new Promise((r) => { resolve = r }))
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const input = screen.getByPlaceholderText(/Rule/) as HTMLInputElement
    const scopeInput = screen.getByPlaceholderText(/Repo scope/) as HTMLInputElement
    const addBtn = screen.getByRole('button', { name: /^Add$/ })
    fireEvent.change(input, { target: { value: 'zzq-inflight-rule' } })
    await userEvent.click(addBtn)

    await waitFor(() => expect(input).toBeDisabled())
    expect(scopeInput).toBeDisabled()
    expect(addBtn).toBeDisabled()

    resolve({ ok: true, outcome: 'inserted', reason: '' })
    await waitFor(() => expect(input).not.toBeDisabled())
    expect(scopeInput).not.toBeDisabled()
  })

  it('keeps a refused lesson editable and reports the backend reason', async () => {
    api.createLesson.mockResolvedValue({
      ok: false, outcome: 'refused', reason: 'blocked_not_clause',
    })
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const reads = api.lessons.mock.calls.length
    const input = screen.getByPlaceholderText(/Rule/) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'zzq-refused-rule' } })
    await userEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /Lesson not saved.*blocked_not_clause.*Edit it and try again/i,
    )
    expect(input.value).toBe('zzq-refused-rule')
    expect(api.lessons).toHaveBeenCalledTimes(reads)
  })

  it('keeps a deduped lesson editable instead of implying it was added', async () => {
    api.createLesson.mockResolvedValue({
      ok: false, outcome: 'deduped', reason: 'substring',
    })
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const reads = api.lessons.mock.calls.length
    const input = screen.getByPlaceholderText(/Rule/) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'zzq-covered-rule' } })
    await userEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      /existing lesson already covers this.*substring/i,
    )
    expect(input.value).toBe('zzq-covered-rule')
    expect(api.lessons).toHaveBeenCalledTimes(reads)
  })

  it('clears an unchanged resubmission but says it was already stored', async () => {
    api.createLesson.mockResolvedValue({
      ok: true, outcome: 'unchanged', reason: 'identical',
    })
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const input = screen.getByPlaceholderText(/Rule/) as HTMLInputElement
    const scopeInput = screen.getByPlaceholderText(/Repo scope/) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'zzq-existing-rule' } })
    fireEvent.change(scopeInput, { target: { value: 'src/kiro_crew' } })
    await userEvent.click(screen.getByRole('button', { name: /^Add$/ }))

    expect(await screen.findByRole('status')).toHaveTextContent(/already stored/i)
    // Both drafts clear on a completed no-op submission, so the retained scope
    // cannot leak into the next, unrelated lesson.
    expect(input.value).toBe('')
    expect(scopeInput.value).toBe('')
  })

  it('refuses to add an empty rule', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    await userEvent.click(screen.getByRole('button', { name: /^Add$/ }))
    expect(api.createLesson).not.toHaveBeenCalled()
  })

  it('deletes the lesson its row names, then re-reads the list', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const reads = api.lessons.mock.calls.length
    const row = screen.getByText('zzq-rule-beta').closest('tr') as HTMLElement
    const del = Array.from(row.querySelectorAll('button'))
      .find((b) => /delete/i.test(b.textContent ?? '')) as HTMLButtonElement
    await userEvent.click(del)

    await waitFor(() => expect(api.deleteLesson).toHaveBeenCalledWith('zzq-rule-beta', null))
    await waitFor(() => expect(api.lessons.mock.calls.length).toBeGreaterThan(reads))
  })

  it('deletes the scoped twin by its scope, sparing the global copy', async () => {
    // A global and a scoped copy of ONE rule: same rule text, distinct scope.
    // The scoped row shows its scope as a chip, which is how a user tells the
    // two apart; deleting it must send that scope so the backend removes only
    // it, not the global twin.
    api.lessons.mockResolvedValue({ lessons: [
      { rule: 'zzq-rule-twin', category: 'knowledge', ts: '2026-01-03T00:00:00Z', repo_scope: null },
      { rule: 'zzq-rule-twin', category: 'knowledge', ts: '2026-01-04T00:00:00Z', repo_scope: 'src/kiro_crew' },
    ] })
    render(<MemoryTab refreshTrigger={0} />)
    const scopeChip = await screen.findByText('src/kiro_crew')
    const scopedRow = scopeChip.closest('tr') as HTMLElement
    const del = Array.from(scopedRow.querySelectorAll('button'))
      .find((b) => /delete/i.test(b.textContent ?? '')) as HTMLButtonElement
    await userEvent.click(del)

    // The scope rides along so the backend deletes by exact (rule, scope)
    // identity, not a scope-blind substring that would take the global twin too.
    await waitFor(() => expect(api.deleteLesson).toHaveBeenCalledWith('zzq-rule-twin', 'src/kiro_crew'))
  })

  it('surfaces a failed delete through ErrorNotice and does not re-read the list', async () => {
    api.deleteLesson.mockRejectedValue(new Error('storage refused the delete'))
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const reads = api.lessons.mock.calls.length
    const row = screen.getByText('zzq-rule-beta').closest('tr') as HTMLElement
    const del = Array.from(row.querySelectorAll('button'))
      .find((b) => /delete/i.test(b.textContent ?? '')) as HTMLButtonElement
    await userEvent.click(del)

    expect(await screen.findByRole('alert')).toHaveTextContent(/storage refused the delete/i)
    // A failed delete removed nothing, so the list is not re-read.
    expect(api.lessons).toHaveBeenCalledTimes(reads)
  })

  it('surfaces an ok:false delete without claiming the row was removed', async () => {
    api.deleteLesson.mockResolvedValue({ ok: false })
    render(<MemoryTab refreshTrigger={0} />)
    await screen.findByText('zzq-rule-beta')
    const reads = api.lessons.mock.calls.length
    const row = screen.getByText('zzq-rule-beta').closest('tr') as HTMLElement
    const del = Array.from(row.querySelectorAll('button'))
      .find((b) => /delete/i.test(b.textContent ?? '')) as HTMLButtonElement
    await userEvent.click(del)

    expect(await screen.findByRole('alert')).toHaveTextContent(/could not delete/i)
    expect(api.lessons).toHaveBeenCalledTimes(reads)
  })

  it('shows an empty state rather than a bare table', async () => {
    api.lessons.mockResolvedValue({ lessons: [] })
    render(<MemoryTab refreshTrigger={0} />)
    expect(await screen.findByText(/No lessons yet/i)).toBeInTheDocument()
  })

  it('tolerates a response with no lessons key', async () => {
    api.lessons.mockResolvedValue({})
    render(<MemoryTab refreshTrigger={0} />)
    expect(await screen.findByText(/No lessons yet/i)).toBeInTheDocument()
  })
})

describe('MemoryTab — manual consolidation', () => {
  it('consolidates every known session and reports the count', async () => {
    render(<MemoryTab refreshTrigger={0} />)
    await userEvent.click(await screen.findByRole('button', { name: /Summarize now/i }))

    await waitFor(() => expect(api.consolidateMemory).toHaveBeenCalledTimes(2))
    expect(api.consolidateMemory).toHaveBeenCalledWith('zzq-s1', true)
    expect(await screen.findByText(/Consolidated/)).toBeInTheDocument()
  })

  it('reports a partial failure instead of claiming success', async () => {
    api.consolidateMemory
      .mockResolvedValueOnce({ ok: true })
      .mockRejectedValueOnce(new Error('zzq-consolidate-failed'))
    render(<MemoryTab refreshTrigger={0} />)
    await userEvent.click(await screen.findByRole('button', { name: /Summarize now/i }))

    const msg = await screen.findByText(/failed/i)
    expect(msg).toBeInTheDocument()
    // The warning tone, not the success one.
    expect((msg.closest('span') as HTMLElement).className).toContain('text-danger')
  })

  it('says there is nothing to consolidate when no session exists', async () => {
    api.sessions.mockResolvedValue({ sessions: [] })
    render(<MemoryTab refreshTrigger={0} />)
    await userEvent.click(await screen.findByRole('button', { name: /Summarize now/i }))
    expect(await screen.findByText(/No sessions to consolidate/i)).toBeInTheDocument()
    expect(api.consolidateMemory).not.toHaveBeenCalled()
  })

  it('treats an unreadable session list as nothing to do rather than crashing', async () => {
    api.sessions.mockRejectedValue(new Error('zzq-sessions-unreachable'))
    render(<MemoryTab refreshTrigger={0} />)
    await userEvent.click(await screen.findByRole('button', { name: /Summarize now/i }))
    expect(await screen.findByText(/No sessions to consolidate/i)).toBeInTheDocument()
  })

  it('clears the outcome message on its own timer', async () => {
    vi.useFakeTimers()
    render(<MemoryTab refreshTrigger={0} />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /Summarize now/i }))
    await act(async () => {})
    expect(screen.getByText(/Consolidated/)).toBeInTheDocument()

    await act(async () => { vi.advanceTimersByTime(4000) })
    expect(screen.queryByText(/Consolidated/)).toBeNull()
  })
})
