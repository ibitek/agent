from synvio.cloud.preview import build_preview


def test_bundle_local_html_css_js_with_restrictive_policy():
    result = build_preview({"index.html": '<html><head><link rel="stylesheet" href="style.css"></head><body><h1>Hello</h1><button onclick="document.body.dataset.clicked=1">Go</button><script src="app.js"></script></body></html>', "style.css": "body {color: red}", "app.js": "console.log('hello')"})
    assert result["available"]
    assert "<h1>Hello</h1>" in result["document"]
    assert "body {color: red}" in result["document"]
    assert "data:text/javascript;base64," in result["document"]
    assert "connect-src &#x27;none&#x27;" in result["document"]
    assert "default-src" in result["document"]


def test_no_fake_preview_for_server_or_unbuilt_react():
    assert not build_preview({"app.py": "print('hi')"})["available"]
    result = build_preview({"index.html": '<script type="module" src="/src/main.jsx"></script>', "src/main.jsx": "export default <App/>"})
    assert not result["available"]
    assert "built" in result["reason"]


def test_remote_assets_and_missing_dependencies_are_not_fetched():
    assert not build_preview({"index.html": '<script src="https://evil.example/script.js"></script>'})["available"]
    assert not build_preview({"index.html": '<script src="missing.js"></script>'})["available"]
    assert not build_preview({"index.html": '<script src="../../private.js"></script>'})["available"]


def test_local_relative_assets_and_meta_refresh_removed():
    result = build_preview({"dist/index.html": '<meta http-equiv="refresh" content="0;url=https://evil.test"><base href="https://evil.test/"><script src="./app.js"></script>', "dist/app.js": "alert(1)"})
    assert result["available"]
    assert "evil.test" not in result["document"]


def test_relative_module_import_requires_build():
    result = build_preview({"index.html": '<script type="module" src="app.js"></script>', "app.js": "import {value} from './dependency.js';"})
    assert not result["available"]


def test_runtime_monitor_is_scoped_and_does_not_weaken_sandbox_policy():
    first=build_preview({'index.html':'<div id="app"></div><script>throw Error("broken")</script>'})
    second=build_preview({'index.html':'<h1>Hello</h1>'})
    assert first['runtime_token']!=second['runtime_token']
    assert 'synvio-preview-runtime' in first['document']
    assert 'unhandledrejection' in first['document']
    assert "connect-src &#x27;none&#x27;" in first['document']
    assert 'http://' not in first['document'] and 'https://' not in first['document']
