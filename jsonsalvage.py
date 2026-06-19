"""Best-effort JSON recovery for model output.

The web-research prompts ask the model to quote reviews/forum posts VERBATIM, which routinely
produces a stray unescaped quote or a missing comma; long answers also get cut off at max_tokens.
A single such hiccup must NEVER crash a run — the deterministic zip scoring is the core deliverable
and has to survive even when a research section's JSON is malformed.

`salvage(text)` tries, in order:
  1. direct json.loads
  2. json.loads of the first {...} / [...] block (strips stray prose)
  3. a missing-comma repair: insert the comma between adjacent values (`}{`, `" "`, `]"`, ...) that
     the model forgot — this is THE dominant "Expecting ',' delimiter" failure on verbatim-quote
     prompts, and recovers the full object instead of dropping the section.
  4. a bracket-balanced repair: walk the text tracking string/brace depth, then close any open
     strings/arrays/objects at the last safe element boundary so a truncated response still parses.
Returns the parsed object, or None if nothing usable can be recovered (callers default with `or {}` /
`or []`). This module has NO heavy imports so it is safe to import from anywhere in the service.
"""
import json, re


def _insert_missing_commas(text):
    """String-aware pass that inserts a comma between two adjacent JSON values whenever the model
    dropped the separator (e.g. `}{`, `} "`, `] [`, `" "` between array items). Only touches gaps
    OUTSIDE strings, and only after a value-ending char (} ] " number/keyword) directly precedes a
    value-starting char ({ [ " - digit t f n). Wrong guesses just fail the re-parse harmlessly."""
    out = []
    in_str = esc = False
    prev = ""  # last significant non-space char outside a string: a value-ender, or "" after a , : or opener
    enders = "}]\"0123456789eltn"   # closers, end of number, end of true/false/null
    starters = "{[\"-0123456789tfn"
    for ch in text:
        if in_str:
            out.append(ch)
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False; prev = '"'
            continue
        if ch.isspace():
            out.append(ch); continue
        if ch == '"':
            if prev and prev in enders:
                out.append(",")
            out.append(ch); in_str = True; prev = ""
            continue
        if ch in "{[":
            if prev and prev in enders:
                out.append(",")
            out.append(ch); prev = ""
            continue
        if ch in ",:":
            out.append(ch); prev = ""
            continue
        if ch in "}]":
            out.append(ch); prev = ch
            continue
        out.append(ch); prev = ch   # number / true / false / null chars
    return "".join(out)


def salvage(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    block = m.group(1) if m else None
    if block:
        try:
            return json.loads(block)
        except Exception:
            pass
    # missing-comma repair (the dominant "Expecting ',' delimiter" cause) — try on the block, then raw
    for candidate in (block, text):
        if not candidate:
            continue
        try:
            return json.loads(_insert_missing_commas(candidate))
        except Exception:
            pass
    # repair truncation: walk the text, track depth, close open structures at the last safe point
    depth = []
    in_str = False
    esc = False
    last_safe = 0
    s = text[text.find("{"):] if "{" in text else text
    for i, ch in enumerate(s):
        if esc:
            esc = False; continue
        if ch == "\\":
            esc = True; continue
        if ch == '"':
            in_str = not in_str; continue
        if in_str:
            continue
        if ch in "{[":
            depth.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if depth: depth.pop()
        if ch == "," and not depth[1:]:  # safe cut point at top-of-array element boundary
            last_safe = i
    for cut in (len(s), last_safe):
        frag = s[:cut].rstrip().rstrip(",")
        if not frag:
            continue
        d = []
        ins = es = False
        for ch in frag:
            if es: es = False; continue
            if ch == "\\": es = True; continue
            if ch == '"': ins = not ins; continue
            if ins: continue
            if ch in "{[": d.append("}" if ch == "{" else "]")
            elif ch in "}]" and d: d.pop()
        repaired = frag + ('"' if ins else "") + "".join(reversed(d))
        try:
            return json.loads(repaired)
        except Exception:
            continue
    return None
