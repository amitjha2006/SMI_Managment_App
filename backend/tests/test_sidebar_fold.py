"""Sidebar fold: collapse to a 64px icon rail on desktop via the menu button.

The header button today only slides the menu on mobile; on desktop it
does nothing. Folding persists per browser and applies before first
paint (no flash). Mobile slide-over behavior is unchanged.

Run: python -m pytest backend/tests/test_sidebar_fold.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_fold_css_collapses_to_rail():
    css = _read("frontend/css/style.css")
    assert "sidebar-collapsed" in css, "no collapsed styles"
    assert "64px" in css, "rail width missing"


def test_toggle_folds_on_desktop_and_persists():
    js = _read("frontend/js/app.js")
    assert "sidebar-collapsed" in js, "toggle ignores collapsed state"
    assert "mf_sidebar" in js, "choice is not remembered"
    index = _read("frontend/index.html")
    assert "mf_sidebar" in index, "flash of full sidebar before paint"
