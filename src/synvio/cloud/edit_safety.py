"""Reject accidental wholesale replacement during a small follow-up edit."""
import re


def check_replacement(path, before, after, request):
    if not before or not path.endswith(('.css', '.jsx', '.tsx', '.js', '.ts', '.html')):
        return
    request = re.sub(r"(?:do not|don't|never|nie)\b[^.!;\n]*", '', request, flags=re.I)
    destructive = re.search(r'\b(?:redesign|rewrite|replace all|from scratch|delete|remove|przebud\w*|od nowa|od zera|usu[nń]\w*|zast[aą]p ca[lł]\w*)\b', request, re.I)
    if destructive:
        return
    if len(before) > 600 and len(after) < len(before) * .6:
        raise ValueError('The replacement removes most of an existing file. Preserve unrelated code and use an exact patch for this follow-up.')
    if path.endswith('.css'):
        selectors = lambda text: set(re.findall(r'([.#][a-zA-Z_][\w-]*)', re.sub(r'/\*.*?\*/', '', text, flags=re.S)))
        old, new = selectors(before), selectors(after)
        if len(old) >= 8 and len(old - new) > len(old) * .25:
            raise ValueError('The CSS replacement drops existing selectors. Keep the working styles and patch only the requested rules.')


def patch_text(content, old, new):
    if not isinstance(old, str) or not old or not isinstance(new, str) or content.count(old) != 1:
        raise ValueError('Patch requires an exact old fragment occurring once. Read the file and retry with enough surrounding context.')
    return content.replace(old, new, 1)
