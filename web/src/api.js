export async function getJSON(url, options) {
  const response = await fetch(url, options)
  const body = await response.json()
  if (response.status === 401) window.dispatchEvent(new Event('synvio-session-expired'))
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`)
  return body
}
