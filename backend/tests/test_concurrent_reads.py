"""Dashboard reads must survive the desktop startup burst.

The pywebview window fires several API requests at once on launch, so
aggregation reads run on threads. The application engine once used
StaticPool: every thread shared a single SQLite connection and concurrent
cursors corrupted each other's result processing (IndexError / ValueError
deep inside SQLAlchemy's result processors). Single-threaded tests could
never see it - only a real concurrent burst, exactly what the first
packaged smoke run tripped over.

Run: python -m pytest backend/tests/test_concurrent_reads.py -v
"""

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _engine(path):
    eng = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA foreign_keys = ON")
        finally:
            cur.close()

    return eng


def test_app_engine_does_not_share_one_connection_across_threads():
    """Pin the pooling decision itself: StaticPool + threads = corruption."""
    from backend.app.core import database

    assert not isinstance(database.engine.pool, StaticPool), (
        "the application engine shares one connection across threads; "
        "concurrent dashboard reads corrupt each other (see test below)"
    )


def test_low_stock_survives_a_startup_burst():
    import backend.app.models  # noqa: F401  (registers every table)
    import backend.main as m
    from backend.app.core.database import Base
    from backend.app.models.finished_product import (
        FinishedProductMovementType,
        FinishedProductStockMovement,
    )
    from backend.app.models.product import PackagingSize, Product

    tmp = os.path.join(tempfile.mkdtemp(prefix="smirace_"), "race.db")
    eng = _engine(tmp)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    try:
        db.add(Product(product_id="P-1", name="Race Masala", is_active=True))
        db.flush()
        prod = db.query(Product).filter(Product.product_id == "P-1").one()
        db.add(
            PackagingSize(
                product_id=prod.id,
                size_name="250g",
                weight_in_kg=0.25,
                threshold_packets=100,
                is_active=True,
            )
        )
        db.flush()
        size = db.query(PackagingSize).filter(PackagingSize.size_name == "250g").one()
        db.add(
            FinishedProductStockMovement(
                product_id=prod.id,
                packaging_size_id=size.id,
                movement_type=FinishedProductMovementType.OPENING,
                quantity=10,  # 10 kg / 0.25 = 40 packets <= 100 threshold: low
            )
        )
        db.commit()
    finally:
        db.close()

    def _burst(_):
        worker = sessionmaker(bind=eng)()
        try:
            return m._low_finished_sizes(worker)
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_burst, range(40)))
    assert all(isinstance(r, list) for r in results)
    assert any(r for r in results), "seeded low stock vanished under concurrency"
    eng.dispose()
