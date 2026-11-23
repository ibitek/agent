const statusLine = document.querySelector("#status");
const share = document.querySelector("#share");
const stop = document.querySelector("#stop");

async function update(operation) {
  share.disabled = stop.disabled = true;
  try {
    const reply = await chrome.runtime.sendMessage({ operation });
    if (!reply?.ok) throw new Error(reply?.error || "The extension did not respond.");
    const session = reply.session;
    stop.hidden = !session;
    statusLine.textContent = session
      ? "Sharing " + session.origin + ". Open Browser > Local tab in Kaelia."
      : "Share the current tab for up to 10 minutes.";
  } catch (error) {
    statusLine.textContent = error.message;
  } finally {
    share.disabled = stop.disabled = false;
  }
}

share.addEventListener("click", () => update("share"));
stop.addEventListener("click", () => update("stop"));
void update("status");
