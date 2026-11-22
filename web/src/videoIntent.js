export function requestsVideoRender(message) {
  const plain = message.toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '').trim()
  if (/\b(?:do not|don't|nie) (?:generate|render|create|wygeneruj|generuj|renderuj)\b/.test(plain)) return false
  return /^(?:(?:can you|could you|please|czy mozesz|mozesz|prosze)\s+)*(?:generate|render|make|create|wygeneruj|wyrenderuj|zrob|stworz)\s+(?:(?:me|mi|a|an|the|new|short|krotk[ai]|now[ey]|pionow[y]|poziom[y])\s+)*(?:video|film|wideo|klip|clip|animation|animacje)\b(?!\s+(?:player|editor|studio|component|app|uploader|generator)\b)/.test(plain)
}
