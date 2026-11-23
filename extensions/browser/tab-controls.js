export function tabControls(operation, selection, snapshotId, approvedOrigin) {
  if (location.origin !== approvedOrigin) throw new Error("The page left the approved website.");
  const safeLink = element => {
    try {
      const target = new URL(element.href, location.href);
      return target.origin === location.origin && target.protocol === "https:" &&
        !element.hasAttribute("download") && (!element.target || element.target === "_self");
    } catch {
      return false;
    }
  };
  const visible = element => element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) &&
    element.getClientRects().length > 0 && !element.closest("[inert], [aria-hidden=true]");
  const label = element => (element.getAttribute("aria-label") || element.innerText || element.title || "")
    .replace(/\s+/g, " ").trim().slice(0, 100);

  const safeResearchLink = element => {
    if (!safeLink(element)) return false;
    const url = new URL(element.href);
    return !url.search && !url.hash && !url.pathname.includes("%") && url.href !== location.href &&
      !/(?:^|[\W_])(?:logout|signout|sign-out|log-out|delete|remove|cancel|refund|purchase|buy|checkout|submit|unsubscribe|settings|payment|admin)(?:$|[\W_])/i.test(url.pathname);
  };

  if (operation === "inspect" || operation === "read") {
    const research = operation === "read";
    const entries = [...document.querySelectorAll("a[href]")].filter(
      element => visible(element) && (research ? safeResearchLink(element) : safeLink(element)) && label(element)
    ).slice(0, 40);
    const id = crypto.randomUUID();
    globalThis.kaeliaTabSnapshot = { id, research, entries: entries.map(element => ({ element, href: element.href })) };
    const result = { snapshot: id, title: document.title.slice(0, 100), origin: location.origin,
      links: entries.map((element, index) => ({ index, label: label(element) })) };
    if (research) {
      const main = document.querySelector("main, article, [role=main]") || document.body;
      const fragments = [...main.querySelectorAll("h1,h2,h3,p,li")]
        .filter(element => visible(element) && !element.closest("form,nav,aside,footer,[contenteditable],dialog"))
        .slice(0, 35).map(element => element.innerText.trim()).filter(Boolean);
      result.text = fragments.join("\n").slice(0, 2400);
      result.address = (location.origin + location.pathname).slice(0, 500);
    }
    return result;
  }
  if (operation === "scroll") {
    window.scrollBy({ top: selection * Math.round(window.innerHeight * 0.75), behavior: "smooth" });
    return {};
  }
  const snapshot = globalThis.kaeliaTabSnapshot;
  const selected = snapshot?.entries[selection];
  const element = selected?.element;
  if (snapshot?.id !== snapshotId || !element?.isConnected || element.href !== selected.href || !visible(element) || !safeLink(element)) {
    throw new Error("The page changed. Inspect the tab again.");
  }
  if (operation === "follow") {
    if (!snapshot.research || !safeResearchLink(element)) throw new Error("This link needs manual navigation.");
    globalThis.kaeliaTabSnapshot = null;
    return { destination: selected.href };
  }
  globalThis.kaeliaTabSnapshot = null;
  element.click();
  return {};
}
