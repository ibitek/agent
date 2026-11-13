"""Self-contained static web previews, returned as JSON, never served on app origin."""
import base64
import posixpath
from html import escape
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

CSP = "default-src 'none'; script-src 'unsafe-inline' data:; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
MIMES = {".svg": "image/svg+xml", ".js": "text/javascript", ".mjs": "text/javascript"}


class NeedsBuild(ValueError):
    pass


class Bundle(HTMLParser):
    def __init__(self, files, entry):
        super().__init__(convert_charrefs=False)
        self.files, self.entry, self.output = files, entry, []

    def local(self, url):
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            raise NeedsBuild("The preview does not fetch external scripts or styles.")
        name = posixpath.normpath(posixpath.join(posixpath.dirname(self.entry), unquote(parsed.path))) if not parsed.path.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(self.entry), unquote(parsed.path).lstrip("/")))
        if name.startswith("../") or name not in self.files:
            raise NeedsBuild("Missing local preview asset: " + name[:120])
        return name, self.files[name]

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "base" or tag == "meta" and values.get("http-equiv", "").lower() in {"refresh", "content-security-policy"}:
            return
        if tag == "script" and values.get("src"):
            name, content = self.local(values["src"])
            extension = posixpath.splitext(name)[1]
            if extension not in {".js", ".mjs"}:
                raise NeedsBuild("This app must be built before it can be previewed.")
            if values.get("type") == "module" and ("import " in content or "import(" in content or "import{" in content):
                raise NeedsBuild("Modules with imports require an application build.")
            values["src"] = "data:text/javascript;base64," + base64.b64encode(content.encode()).decode()
            values.pop("integrity", None)
            values.pop("crossorigin", None)
        if tag == "link" and values.get("rel", "").lower() == "stylesheet":
            _, content = self.local(values.get("href", ""))
            self.output.append("<style>" + content.replace("</style", "<\\/style") + "</style>")
            return
        if tag == "img" and values.get("src") and not values["src"].startswith("data:"):
            try:
                name, content = self.local(values["src"])
                mime = MIMES.get(posixpath.splitext(name)[1], "text/plain")
                values["src"] = "data:" + mime + ";base64," + base64.b64encode(content.encode()).decode()
            except NeedsBuild:
                values.pop("src", None)
        if tag == "a":
            values["href"] = "#"
            values.pop("target", None)
        attrs = "".join(" " + key + ("=\"" + escape(value, quote=True) + "\"" if value is not None else "") for key, value in values.items())
        self.output.append("<" + tag + attrs + ">")

    def handle_endtag(self, tag):
        self.output.append("</" + tag + ">")

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append("&" + name + ";")

    def handle_charref(self, name):
        self.output.append("&#" + name + ";")


def build_preview(files):
    entry = next((name for name in ("dist/index.html", "web/dist/index.html", "index.html", "public/index.html") if name in files), None)
    if not entry:
        return {"available": False, "reason": "No built index.html was found. React, Python and server apps need a connected runtime."}
    try:
        parser = Bundle(files, entry)
        parser.feed(files[entry])
        parser.close()
    except (NeedsBuild, ValueError) as exc:
        return {"available": False, "reason": str(exc)}
    document = "<!doctype html><meta charset=utf-8><meta http-equiv=Content-Security-Policy content=\"" + escape(CSP, quote=True) + "\">" + "".join(parser.output)
    if len(document.encode()) > 3 * 1024 * 1024:
        return {"available": False, "reason": "The preview exceeds the size limit."}
    return {"available": True, "kind": "static_web", "entry": entry, "document": document}
