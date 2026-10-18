"""Read a string value out of JSON that is still being streamed, so it can be shown as it is written."""

from __future__ import annotations

import re

_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def partial_string_value(text: str, key: str) -> str | None:
    """The first `"key": "..."` string value in `text` as far as it has been written, unescaped;
    None until its opening quote has arrived. A half-written escape at the end is left out."""
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', text)
    if not match:
        return None
    out: list[str] = []
    i = match.end()
    while i < len(text):
        ch = text[i]
        if ch == '"':
            break
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(text):
            break
        nxt = text[i + 1]
        if nxt == "u":
            code = text[i + 2 : i + 6]
            if len(code) < 4:
                break
            try:
                out.append(chr(int(code, 16)))
            except ValueError:
                pass
            i += 6
            continue
        out.append(_ESCAPES.get(nxt, nxt))
        i += 2
    return "".join(out)
