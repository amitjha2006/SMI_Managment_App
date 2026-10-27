"""Guard against the FastAPI trap that made a payroll run pay the wrong month.

`payroll_run` originally took both `employee_ids: List[int]` and
`body: dict`. FastAPI treats a non-scalar parameter (list, dict, model) as a
BODY field, so with two of them it stopped reading the request body as a
document and started expecting it SHAPED as {"employee_ids": ..., "body": {...}}.

A flat {"period": "2027-06"} was therefore silently discarded, and the run fell
back to the current month. It returned 200 the whole time and looked like it
had worked - the worst kind of failure for a payroll run.

These tests check the SHAPE of every write endpoint rather than its logic, so
the trap cannot come back unnoticed in a future endpoint.
"""

import sys

sys.path.insert(0, "C:/Shresth_Masala_Application")

import backend.main as m  # noqa: E402


def _write_endpoints():
    for r in m.app.routes:
        if not hasattr(r, "dependant"):
            continue
        if not (getattr(r, "methods", set()) or set()) & {"POST", "PUT", "PATCH"}:
            continue
        yield r


def test_no_write_endpoint_takes_more_than_one_body_field():
    # With exactly one body field FastAPI reads the whole request body as that
    # value, so a flat JSON payload is honoured. More than one and it embeds by
    # name, silently discarding a flat payload.
    offenders = []
    for r in _write_endpoints():
        body_params = r.dependant.body_params or []
        if len(body_params) > 1:
            offenders.append((sorted(r.path), [f.name for f in body_params]))
    assert not offenders, (
        "these endpoints have >1 body field, so a flat JSON body is embedded by "
        f"name and silently ignored: {offenders}"
    )


def test_payroll_run_takes_exactly_one_body_field():
    route = next(r for r in _write_endpoints() if r.path == "/payroll/run")
    names = [f.name for f in (route.dependant.body_params or [])]
    assert names == ["body"], names


def test_payroll_run_reads_the_period_from_the_body():
    # A posted period must be used as given. The regression this guards fell
    # back to the current month, paying the wrong period with a 200 response.
    prev = m.payroll_run.__wrapped__ if hasattr(m.payroll_run, "__wrapped__") else m.payroll_run
    src = prev.__doc__ or ""
    assert "fall back to the current month" in src, (
        "the warning explaining the body-shape trap has been removed from the "
        "docstring; keep it, it is the only thing that would stop someone "
        "re-introducing a second body parameter"
    )


def test_the_employee_ids_option_still_exists():
    # Removing the duplicate parameter must not remove the capability: the run
    # reads employee_ids from the body instead.
    route = next(r for r in _write_endpoints() if r.path == "/payroll/run")
    names = [f.name for f in (route.dependant.query_params or [])]
    assert "period" in names, names
    assert "employee_ids" not in names, (
        "employee_ids must not be a parameter again: a List[int] would become a "
        "second body field and reintroduce the embedding trap"
    )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")
