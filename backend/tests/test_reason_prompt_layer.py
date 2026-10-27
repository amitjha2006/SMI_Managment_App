"""The mandatory-reason prompt must sit above every modal.

The prompt dialog (components.js reasonPrompt) once rendered at z-index 220
while .modal-overlay (style.css) stacks at 1200, so asking for the change
note from inside the pack-size editor, workflow editor, opening stock or the
receive picker drew the dialog *behind* the modal: Save appeared to do
nothing because the hidden gate waited for a note nobody could type.

Pin: the reason overlay's z-index must be numerically greater than the
modal overlay's, in the shipped files.

Run: python -m pytest backend/tests/test_reason_prompt_layer.py -v
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_reason_prompt_above_modals():
    js = _read("frontend", "js", "components.js")
    css = _read("frontend", "css", "style.css")

    modal = int(re.search(
        r"\.modal-overlay\s*\{[^}]*z-index:\s*(\d+)", css, re.S).group(1))

    # reasonPrompt is the only overlay that carries the #dlg-reason textarea;
    # take the z-index from its inline cssText, not some other dialog.
    m = re.search(r"function reasonPrompt\((?:.|\n)*?z-index:(\d+)", js)
    assert m, "reasonPrompt cssText lost its inline z-index"
    reason = int(m.group(1))

    assert reason > modal, (
        f"reason prompt z-index {reason} must beat .modal-overlay {modal} "
        "(the note went behind open dialogs once already)"
    )


def test_reason_textarea_gets_spellcheck():
    """WebView2 doesn't reliably fall back to the browser default, so the
    note textarea must ask for spell checking explicitly."""
    js = _read("frontend", "js", "components.js")
    i = js.find('id="dlg-reason"')
    assert i >= 0, "reason textarea moved"
    block = js[i:i + 400]
    assert re.search(r'spellcheck\s*=\s*["\']?true', block), (
        "#dlg-reason lost spellcheck - the packaged window may not flag typos"
    )


def test_every_textarea_gets_spellcheck_on_focus():
    """Notes/remarks boxes elsewhere have no spellcheck attribute and relied
    on WebView2 defaults; the delegated focusin handler covers all of them."""
    js = _read("frontend", "js", "components.js")
    i = js.find('addEventListener("focusin"')
    assert i >= 0, "delegated textarea spellcheck went missing"
    block = js[i:i + 500]
    assert 'tagName === "TEXTAREA"' in block, (
        "focusin handler no longer targets textareas"
    )
    assert "t.spellcheck = true" in block, "focusin handler no longer enables spellcheck"
