// Apply before React mounts so saved light mode does not flash dark.
let theme='dark'
try{
  const saved=localStorage.getItem('synvio-theme-v1')
  theme=['light','dark'].includes(saved)?saved:window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'
}catch{/* The interface works without local storage. */}
document.documentElement.dataset.theme=theme
document.documentElement.style.colorScheme=theme
