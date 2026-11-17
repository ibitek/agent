const text = value => typeof value === 'string' ? value.replace(/\u001b\[[0-9;]*[A-Za-z]/g, '').slice(0, 12000) : ''

// Only explicit execution events become activity. Cost records, reasoning and
// unknown provider payloads must never be rendered as an action or raw JSON.
export function activityItems(events = []) {
  return events.flatMap((event, index) => {
    let category, title, detail = '', output = '', state = 'info'
    const path = text(event.path || event.arguments?.path)
    if (event.event === 'tool_call' && ['dev_read_file', 'dev_write_file', 'dev_delete_file', 'dev_list_files'].includes(event.tool)) {
      category = 'files'
      title = {dev_read_file:'Read file', dev_write_file:'Saved file', dev_delete_file:'Deleted file', dev_list_files:'Listed files'}[event.tool]
      if (event.success === false) { title = 'File operation failed'; state = 'error' }
      detail = path
    } else if (event.event === 'command_started' || event.event === 'command_result') {
      category = 'commands'; detail = text(event.command)
      if (event.event === 'command_started') title = 'Command started'
      else {
        title = event.exit_code == null ? 'Command returned without an exit status' : `Command finished · exit ${event.exit_code}`
        state = event.exit_code == null ? 'info' : event.exit_code === 0 ? 'success' : 'error'
        output = text(event.output)
      }
    } else if (event.event === 'browser_screenshot') {
      category = 'browser'; title = 'Captured browser screenshot'; detail = text(event.text)
    } else if (['preview_source_check', 'application_source_check'].includes(event.event)) {
      category = 'checks'; title = event.success === true ? 'Source check passed' : 'Source check needs attention'
      detail = text(event.error); state = event.success === true ? 'success' : 'error'
    } else if (event.event === 'strategy_change') {
      category = 'checks'; title = 'Changing approach'; detail = text(event.text)
    } else if (['task_error', 'edit_rejected', 'repair_limit_reached'].includes(event.event)) {
      category = 'checks'; title = {task_error:'Work interrupted', edit_rejected:'Edit rejected · files unchanged', repair_limit_reached:'Repair safety limit reached'}[event.event]
      detail = text(event.error || event.reason); state = 'error'
    } else if (event.event === 'codex_workspace') {
      category = 'commands'; title = 'Codex workspace connected'; detail = text(event.runtime)
    } else return []
    return [{key: event.sequence ?? `${index}:${event.event}`, category, title, detail, output, state,
      time: typeof event.created_at === 'number' ? event.created_at : null,
      path: category === 'files' && event.tool !== 'dev_delete_file' && event.success !== false ? path : ''}]
  })
}
