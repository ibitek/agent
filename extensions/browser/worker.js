import { tabControls } from "./tab-controls.js";

const workspaceOrigin = "https://agents.synviocore.xyz";
const lifetime = 10 * 60 * 1000;
let queue = Promise.resolve();

async function dismiss(session) {
  await chrome.storage.session.remove("localBrowser");
  if (session?.target) await chrome.action.setBadgeText({ tabId: session.target, text: "" }).catch(() => {});
}

async function currentSession() {
  const { localBrowser: session } = await chrome.storage.session.get("localBrowser");
  if (session && session.expires > Date.now()) return session;
  if (session) await dismiss(session);
  return null;
}

function respond(work, callback) {
  queue = queue.then(work).then(result => callback({ ok: true, ...result }),
    error => callback({ ok: false, error: error.message }));
  return true;
}

chrome.runtime.onMessage.addListener((message, sender, callback) => {
  if (sender.id !== chrome.runtime.id) return;
  if (sender.origin === workspaceOrigin && sender.frameId === 0 && sender.tab?.id && sender.documentId) {
    return respond(() => workspaceAction(message, sender), callback);
  }
  if (sender.url !== chrome.runtime.getURL("popup.html")) return;
  return respond(async () => {
    const previous = await currentSession();
    switch (message?.operation) {
      case "status":
        return { session: previous };
      case "stop":
        await dismiss(previous);
        return {};
      case "share": {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        const target = tab?.id;
        const origin = new URL(tab?.url || "about:blank").origin;
        if (!target || !origin.startsWith("https://") || origin === workspaceOrigin) {
          throw new Error("Open the HTTPS page you want to share, then use the extension there.");
        }
        const workspaces = await chrome.tabs.query({ currentWindow: true, url: workspaceOrigin + "/*" });
        const workspace = workspaces.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
        if (!workspace?.id) throw new Error("Open Kaelia in this browser window before sharing a tab.");
        await dismiss(previous);
        const session = { target, origin, id: crypto.randomUUID(), expires: Date.now() + lifetime,
          workspaceTab: workspace.id };
        await chrome.storage.session.set({ localBrowser: session });
        await chrome.action.setBadgeText({ tabId: target, text: "ON" });
        return { session };
      }
      default:
        throw new Error("Unknown extension action.");
    }
  }, callback);
});

async function workspaceAction(message, sender) {
  const session = await currentSession();
  const ownTab = session?.workspaceTab === sender.tab.id;
  if (message?.operation === "status") {
    if (!ownTab || (session.document && session.document !== sender.documentId)) return { session: null };
    const target = await chrome.tabs.get(session.target).catch(() => null);
    if (!target?.url || new URL(target.url).origin !== session.origin) {
      await dismiss(session);
      return { session: null };
    }
    if (!session.document) {
      session.document = sender.documentId;
      await chrome.storage.session.set({ localBrowser: session });
    }
    return { session: { id: session.id, origin: session.origin, expires: session.expires } };
  }
  if (!ownTab || session.document !== sender.documentId || message?.sessionId !== session.id) {
    throw new Error("Tab sharing ended. Share the tab again.");
  }
  if (message.operation === "stop") {
    await dismiss(session);
    return {};
  }
  const targetTab = await chrome.tabs.get(session.target).catch(() => null);
  if (!targetTab?.url || new URL(targetTab.url).origin !== session.origin) {
    await dismiss(session);
    throw new Error("The shared tab was closed or left the approved website.");
  }
  const { operation, selection, snapshot } = message;
  if (operation !== "inspect" && operation !== "read" && !(operation === "scroll" && [-1, 1].includes(selection)) &&
      !((operation === "click" || operation === "follow") && Number.isInteger(selection) && selection >= 0 && selection < 40 &&
        typeof snapshot === "string" && snapshot.length === 36)) {
    throw new Error("That browser action is unavailable.");
  }
  if (operation === "read" && (targetTab.status === "loading" || targetTab.pendingUrl ||
      (message.expected && targetTab.url !== message.expected))) return { waiting: true };
  const [injection] = await chrome.scripting.executeScript({
    target: { tabId: session.target, frameIds: [0] }, func: tabControls,
    args: [operation, selection ?? null, snapshot ?? null, session.origin]
  });
  if (!injection?.result) throw new Error("The shared page is no longer available. Inspect or share it again.");
  if (operation === "follow") {
    const destination = new URL(injection.result.destination);
    if (destination.origin !== session.origin || destination.search || destination.hash) {
      throw new Error("That destination is outside the research scope.");
    }
    const latest = await chrome.tabs.get(session.target);
    if (latest.status === "loading" || latest.pendingUrl || latest.url !== targetTab.url) {
      throw new Error("The shared page changed. Start the research again.");
    }
    await chrome.tabs.update(session.target, { url: destination.href });
  }
  return injection.result;
}

function onTabChanged(tab, url) {
  queue = queue.then(async () => {
    const session = await currentSession();
    if (session && (session.target === tab && (!url || new URL(url).origin !== session.origin) ||
        session.workspaceTab === tab)) await dismiss(session);
  }).catch(() => {});
}

chrome.tabs.onRemoved.addListener(tab => onTabChanged(tab));
chrome.tabs.onUpdated.addListener((tab, event) => {
  if (event.url) onTabChanged(tab, event.url);
});
