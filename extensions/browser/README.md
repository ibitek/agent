# Kaelia Local Browser

Early preview for desktop Chrome and Edge. Works with `https://agents.synviocore.xyz`.

1. Open **Extensions → Manage extensions** and enable developer mode.
2. Choose **Load unpacked** and select this folder.
3. Open an HTTPS website. Click **Kaelia Local Browser** in the toolbar and choose **Share this tab**.
4. Copy the temporary code into **Kaelia → Browser → Local tab**, then choose **Connect tab**.
5. Inspect the page, scroll, or select a same-website link. Activity appears in Kaelia. Use **Stop** in either the workspace or the extension.

Sharing expires after ten minutes or when the shared tab closes or changes to a different origin. The code is held in browser session memory, binds to one Kaelia page, and never goes to the Kaelia server. Reloading Kaelia requires sharing again. Page inspection returns the title and up to 40 eligible link labels. Manual inspection stays in the browser. Starting **Research tab** sends the research goal and bounded visible text and links from the shared website to Kaelia's configured model. Kaelia can follow eligible same-site links, report what it found, or ask the user to share another tab. It cannot submit forms. Pause stops the next step; a provider request already sent may still finish. If Kaelia asks for a new website, stop sharing, pair the new tab and press **Resume**. You can prefill a request using `/browse your request` in the main chat.
