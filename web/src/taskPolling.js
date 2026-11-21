const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'paused'])
const FINISHED = new Set(['task_finished', 'task_error', 'task_cancelled'])

export function mergeTasks(current, incoming) {
  const byId = new Map(current.map((task) => [task.id, task]))
  for (const task of incoming) {
    const previous = byId.get(task.id)
    if (!previous || (task.updated_at || 0) > (previous.updated_at || 0) ||
        ((task.updated_at || 0) === (previous.updated_at || 0) && !TERMINAL.has(previous.status))) {
      byId.set(task.id, task)
    }
  }
  return [...byId.values()].sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
}

export function watchTask(id, { getJSON, onSnapshot, schedule = setTimeout, cancel = clearTimeout }) {
  const controller = new AbortController()
  let timer, cursor = 0, quiet = 0, failures = 0
  const events = new Map()
  async function poll() {
    const previousCursor = cursor
    try {
      // Read status first, then drain the log; a terminal status must not stop log fetching.
      const task = await getJSON(`/api/tasks/${id}`, { signal: controller.signal })
      let page
      do {
        page = await getJSON(`/api/tasks/${id}/events?after=${cursor}`, { signal: controller.signal })
        if (controller.signal.aborted) return
        for (const event of page.items) {
          events.set(event.sequence, event)
          cursor = Math.max(cursor, event.sequence)
        }
      } while (page.items.length === 500)
      if (controller.signal.aborted) return
      const items = [...events.values()].sort((a, b) => a.sequence - b.sequence)
      const complete = TERMINAL.has(task.status) && !task.build_pending && !task.compute_usage?.active && items.some((event) => FINISHED.has(event.event))
      onSnapshot({ task, events: items, complete })
      failures = 0
      quiet = cursor === previousCursor ? Math.min(quiet + 1, 4) : 0
      if (complete) return
    } catch {
      // Keep the last confirmed snapshot and retry; network errors are not task failures.
      failures = Math.min(failures + 1, 4)
    }
    if (!controller.signal.aborted) timer = schedule(poll, failures ? Math.min(15000, 1500 * 2 ** failures) : 1200 + quiet * 900)
  }
  timer = schedule(poll, 0)
  return () => { controller.abort(); cancel(timer) }
}
