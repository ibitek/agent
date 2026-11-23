window.addEventListener("message", async event => {
  if (event.source !== window || event.origin !== location.origin ||
      event.data?.channel !== "kaelia-browser-request" || typeof event.data.id !== "string") return;
  const { id, channel, ...message } = event.data;
  let reply;
  try {
    reply = await chrome.runtime.sendMessage(message);
  } catch {
    reply = { ok: false, error: "The browser extension was disconnected." };
  }
  window.postMessage({ channel: "kaelia-browser-reply", id, reply }, location.origin);
});
