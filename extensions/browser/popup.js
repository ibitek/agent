const statusLine = document.querySelector("#status");
const pairing = document.querySelector("#pairing");
const code = document.querySelector("#code");
const share = document.querySelector("#share");
const copy = document.querySelector("#copy");
const stop = document.querySelector("#stop");

async function update(operation) {
  share.disabled = copy.disabled = stop.disabled = true;
  try {
    const reply = await chrome.runtime.sendMessage({ operation });
    if (!reply?.ok) throw new Error(reply?.error || "The extension did not respond.");
    const session = reply.session;
    pairing.hidden = copy.hidden = stop.hidden = !session;
    code.value = session ? chrome.runtime.id + ":" + session.code : "";
    statusLine.textContent = session
      ? "Sharing " + session.origin + ". Paste the pairing code in Kaelia → Browser → Local tab."
      : "Share the current tab for up to 10 minutes.";
  } catch (error) {
    statusLine.textContent = error.message;
  } finally {
    share.disabled = copy.disabled = stop.disabled = false;
  }
}

share.addEventListener("click", () => update("share"));
stop.addEventListener("click", () => update("stop"));
copy.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(code.value);
    statusLine.textContent = "Code copied. Paste it in Kaelia.";
  } catch {
    code.select();
    statusLine.textContent = "Copy the selected code, then paste it in Kaelia.";
  }
});
void update("status");
