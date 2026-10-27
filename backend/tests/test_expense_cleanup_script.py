"""--rename-payee accepts several OLD=NEW pairs, so one run merges every
misspelling of a name against a single backup.

Run: python -m pytest backend/tests/test_expense_cleanup_script.py -v
"""

import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_ROOT, "scripts", "fix_expense_cleanup.py")


def _load():
    spec = importlib.util.spec_from_file_location("fix_expense_cleanup", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_pair():
    assert _load().parse_payees("mayself=MySelf") == [("mayself", "MySelf")]


def test_several_pairs_merge_in_one_run():
    assert _load().parse_payees(" manjeet = Manjeet, manjt=Manjeet ,manjit=Manjeet") == [
        ("manjeet", "Manjeet"),
        ("manjt", "Manjeet"),
        ("manjit", "Manjeet"),
    ]


def test_no_flag_is_no_work():
    assert _load().parse_payees("") == []


def test_a_pair_without_a_name_is_refused():
    module = _load()
    for bad in ("manjt", "manjt=", "=Manjeet"):
        try:
            module.parse_payees(bad)
        except SystemExit:
            continue
        raise AssertionError(f"{bad!r} should be refused")
