export const SPEND_CAP_KEY = 'synvio-spend-cap-v2'
export const DEFAULT_SPEND_CAP = '0.20'
export function validSpendCap(value) {
  return value === 'unlimited' || (String(value).trim() !== '' && Number.isFinite(Number(value)) && Number(value) > 0)
}
export function readSpendCap() {
  try { const saved = localStorage.getItem(SPEND_CAP_KEY); return validSpendCap(saved) ? saved : DEFAULT_SPEND_CAP } catch { return DEFAULT_SPEND_CAP }
}
export function saveSpendCap(value) {
  if (!validSpendCap(value)) return false
  try { localStorage.setItem(SPEND_CAP_KEY, value) } catch { /* Keep the in-memory setting when storage is unavailable. */ }
  return true
}
export const providerBudget = value => value === 'unlimited' ? null : Number(value) / 1.3
export const formatSpendCap = value => value === 'unlimited' ? 'Unlimited' : `$${Number(value).toFixed(2)}`
