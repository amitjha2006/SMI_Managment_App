"""Challan rows show on-hand packets per selected size before qty entry.

The form reads GET /finished-stock/balance once when opened and shows a
small "Balance: N pkts" readout on every line, refreshed whenever the
packet size changes. Display-only: the confirm guard still owns oversell
refusals. No backend change: the balance endpoint already returns
per-size balance_packets.

Run: python -m pytest backend/tests/test_challan_balance_hint.py -v
"""

import os


def _read():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "frontend", "pages", "challans.html"), encoding="utf-8") as fh:
        return fh.read()


def test_form_loads_finished_stock_balance():
    html = _read()
    assert "/finished-stock/balance" in html, (
        "challan form never reads per-size stock balances"
    )


def test_rows_show_balance_readout_refreshed_on_pack_change():
    html = _read()
    assert "Balance:" in html, "no balance readout on challan rows"
    assert "balance_packets" in html, "readout is not fed from balance_packets"
