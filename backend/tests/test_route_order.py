"""Test that routes are registered in correct order (static before parameterized).

This prevents the route order trap where /import/undo must come before /import/{import_type}.
"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app


def test_import_undo_before_parameterized():
    """Ensure /import/undo-status is matched before /import/{import_type}."""
    client = TestClient(app)
    # The static path /import/undo-status should be registered and match
    # If parameterized /import/{import_type} was registered first, it would capture "undo-status"
    response = client.get("/import/undo-status", headers={"Authorization": "Bearer invalid"})
    # We expect 401 (auth required), not 422 (validation error from treating "undo-status" as import_type)
    assert response.status_code in (401, 403), f"Expected auth error, got {response.status_code}: {response.text}"


def test_static_routes_before_parameterized():
    """Verify all known static routes are registered before their parameterized siblings by making HTTP requests."""
    client = TestClient(app)
    
    # These static routes should return 401/403 (auth required), not 422 (validation error)
    # If parameterized route was first, it would try to parse the static path as import_type
    static_routes = [
        "/import/types",
        "/import/reports", 
        "/import/undo-status",
    ]
    
    for path in static_routes:
        response = client.get(path, headers={"Authorization": "Bearer invalid"})
        # Should be auth error, not validation error
        assert response.status_code in (401, 403), (
            f"Route {path} returned {response.status_code} (expected 401/403). "
            f"This suggests parameterized route /import/{{import_type}} is catching it first. "
            f"Body: {response.text}"
        )


def test_admin_only_prefixes_exist():
    """Verify ADMIN_ONLY_PREFIXES in permissions.py covers all admin-only routes."""
    from backend.app.core.permissions import ADMIN_ONLY_PREFIXES

    # These prefixes should be in the centralized list
    required_prefixes = ["/users", "/backup", "/settings"]
    for prefix in required_prefixes:
        assert prefix in ADMIN_ONLY_PREFIXES, f"Missing admin prefix: {prefix}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])