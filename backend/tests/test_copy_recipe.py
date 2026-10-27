"""One-click recipe copy in the product editor.

A mix masala with 15 ingredients means ~40 clicks today. The editor now
offers "Copy recipe from…" at the top: pick a product that already has a
formulation and its ingredients (+ steps) fill in, matched by material
with a warning for anything missing. Saving still goes through the
existing PUT, so Editor Mode and role rules apply unchanged.

Run: python -m pytest backend/tests/test_copy_recipe.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, *relpath.split("/")), encoding="utf-8") as fh:
        return fh.read()


def test_copy_recipe_control_at_editor_top():
    html = _read("frontend/pages/inventory.html")
    i = html.find("copy-recipe-from")
    assert i >= 0, "no copy-recipe control"
    # At the top of the size editor: before the Product name field.
    assert i < html.find('id="se-name"'), "control is not at the top of the editor"


def test_copy_recipe_fills_rows_and_steps():
    html = _read("frontend/pages/inventory.html")
    assert "applyCopiedRecipe" in html, "no apply handler"
    assert "/formulation" in html, "never reads the source recipe"
    assert "addFormulationRow" in html and "addFormulationStep" in html


def test_copy_recipe_warns_on_missing_materials():
    html = _read("frontend/pages/inventory.html")
    i = html.find("window.applyCopiedRecipe")
    assert i >= 0
    block = html[i:i + 3500]
    assert "skipped" in block.lower(), "missing materials fail silently"
