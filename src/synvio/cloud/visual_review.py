"""Trusted Playwright helper copied to a VM; never runs on the API host."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit


def local_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password:
        raise ValueError('Render checks require an HTTP preview on 127.0.0.1.')
    return value


def inspect(playwright, url, root):
    """Render evidence only. This is not a claim of functional or aesthetic QA."""
    local_url(url)
    root = Path(root)
    artifacts = root / '.kaelia'
    artifacts.mkdir(exist_ok=True)
    browser = playwright.chromium.launch(headless=True)
    views = []
    try:
        for name, width, height in [('desktop', 1440, 1000), ('mobile', 390, 844)]:
            context = browser.new_context(viewport={'width': width, 'height': height},
                                          reduced_motion='reduce', service_workers='block', accept_downloads=False)
            errors = []
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)[:800]) if len(errors) < 20 else None)
            page.on('console', lambda message: errors.append(message.text[:800]) if message.type == 'error' and len(errors) < 20 else None)
            page.on('dialog', lambda dialog: dialog.dismiss())
            def allow(route):
                target = urlsplit(route.request.url)
                if target.scheme in {'data', 'blob'} or (target.scheme == 'http' and target.hostname == '127.0.0.1' and target.port == urlsplit(url).port):
                    route.continue_()
                else:
                    route.abort()
            context.route('**/*', allow)
            response = page.goto(url, wait_until='networkidle', timeout=12000)
            metrics = page.evaluate('''() => ({title: document.title,
                textLength: (document.body?.innerText || '').trim().length,
                horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 2,
                headings: document.querySelectorAll('h1').length})''')
            filename = 'synvio-preview.png' if name == 'desktop' else 'kaelia-mobile.png'
            page.screenshot(path=str(root / filename), full_page=False, animations='disabled')
            issues = list(errors)
            if response is None or response.status >= 400: issues.append('The preview did not return a successful HTTP response.')
            if metrics['horizontalOverflow']: issues.append('Horizontal overflow detected.')
            if metrics['textLength'] < 40: issues.append('Very little rendered text; inspect for an empty or incomplete page.')
            views.append({'viewport': name, 'width': width, 'height': height, 'screenshot': filename,
                          'metrics': metrics, 'issues': issues[:25]})
            context.close()
    finally:
        browser.close()
    result = {'version': 1, 'captured_at': time.time(), 'url': url, 'views': views,
              'passed': all(not item['issues'] for item in views),
              'interaction_tests_run': False, 'visual_review_required': True}
    (artifacts / 'visual-review.json').write_text(json.dumps(result), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5173')
    parser.add_argument('--root', default='/vercel/sandbox/project')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        result = inspect(playwright, args.url, args.root)
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
