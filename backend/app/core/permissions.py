"""Permission catalogue and resolver (spec 3: "Manager access is configurable
by Admin").

The `defaults` below encode the role behaviour that is hardcoded in
`rbac_guard` today. They are only fallbacks: once an Admin saves a choice on
the Permissions screen, that explicit choice wins. With no configuration at
all, behaviour is exactly what it is now.
"""

from backend.app.models.user import RolePermission

ROLES = ["admin", "manager", "data_entry", "viewer", "challan_entry"]

# Centralized admin-only route prefixes (used by rbac_guard in main.py)
# Adding a new admin-only route: add its prefix here.
ADMIN_ONLY_PREFIXES = (
    "/users",
    "/backup",
    "/settings",
)

# Every one of the original four roles can read. The middleware only restricts
# non-GET methods, so those roles hold the read permissions by default -
# otherwise the matrix would claim a Viewer can see nothing, which is not how
# the app behaves. challan_entry is deliberately NOT in ALL: its middleware
# allowlist is far stricter than "can read", and the matrix must not claim
# otherwise.
ALL = {"admin": True, "manager": True, "data_entry": True, "viewer": True}

# name, module, action, description, {role: default}  # noqa: ERA001 - tuple shape doc
PERMISSIONS = [
    # --- read access (all roles) ---
    (
        "masters.view",
        "masters",
        "view",
        "View customers, suppliers, products and raw materials",
        dict(ALL, challan_entry=True),
    ),
    ("purchases.view", "purchases", "view", "View purchase invoices", ALL),
    ("production.view", "production", "view", "View production entries and wastage", ALL),
    (
        "sales.view",
        "sales",
        "view",
        "View sales challans",
        dict(ALL, challan_entry=True),
    ),
    ("inventory.view", "inventory", "view", "View stock and movement history", ALL),
    ("expenses.view", "expenses", "view", "View expenses", ALL),
    ("employees.view", "employees", "view", "View employees and payroll", ALL),
    ("reports.view", "reports", "view", "View dashboards, reports and exports", ALL),
    # --- customers / suppliers / products (master data) ---
    (
        "masters.manage",
        "masters",
        "manage",
        "Create and edit customers, suppliers, products, raw materials",
        {"admin": True, "manager": True, "data_entry": True},
    ),
    (
        "masters.delete",
        "masters",
        "delete",
        "Delete master records",
        {"admin": True, "manager": True},
    ),
    # --- purchases ---
    (
        "purchases.manage",
        "purchases",
        "manage",
        "Create purchase invoices",
        {"admin": True, "manager": True, "data_entry": True},
    ),
    (
        "purchases.pay",
        "purchases",
        "pay",
        "Record supplier payments",
        {"admin": True, "manager": True},
    ),
    # --- production ---
    (
        "production.manage",
        "production",
        "manage",
        "Create production entries",
        {"admin": True, "manager": True, "data_entry": True},
    ),
    (
        "production.approve",
        "production",
        "approve",
        "Start, complete or cancel production entries",
        {"admin": True, "manager": True},
    ),
    # --- sales ---
    (
        "sales.manage",
        "sales",
        "manage",
        "Create and edit sales challans",
        {"admin": True, "manager": True, "data_entry": True, "challan_entry": True},
    ),
    (
        "sales.confirm",
        "sales",
        "confirm",
        "Confirm a challan (posts stock and receivables)",
        {"admin": True, "manager": True},
    ),
    (
        "sales.dispatch",
        "sales",
        "dispatch",
        "Dispatch and mark a challan delivered",
        {"admin": True, "manager": True},
    ),
    (
        "sales.collect",
        "sales",
        "collect",
        "Record customer payments",
        {"admin": True, "manager": True},
    ),
    # --- inventory ---
    (
        "inventory.adjust",
        "inventory",
        "adjust",
        "Adjust stock or record opening stock",
        {"admin": True, "manager": True},
    ),
    # --- expenses / employees ---
    (
        "expenses.manage",
        "expenses",
        "manage",
        "Record expenses",
        {"admin": True, "manager": True, "data_entry": True},
    ),
    ("expenses.delete", "expenses", "delete", "Archive expenses", {"admin": True, "manager": True}),
    (
        "employees.manage",
        "employees",
        "manage",
        "Manage employees, attendance and salary",
        {"admin": True, "manager": True, "data_entry": True},
    ),
    # Payroll is separated from employees.manage on purpose: that permission
    # covers the employee master and attendance, which data entry legitimately
    # needs, but RUNNING payroll pays real money to staff. Borrowing
    # employees.manage for it would hand the lowest role the ability to pay
    # everyone, so this is its own gate and data entry is excluded.
    (
        "payroll.manage",
        "payroll",
        "manage",
        "Run payroll, record advances and deductions",
        {"admin": True, "manager": True},
    ),
    # --- archive (soft delete) ---
    (
        "records.archive",
        "records",
        "archive",
        "Archive financial and inventory records",
        {"admin": True, "manager": True},
    ),
    (
        "records.restore",
        "records",
        "restore",
        "Bring archived records back into the active list",
        {"admin": True, "manager": True},
    ),
    # --- reports / sharing ---
    (
        "reports.custom",
        "reports",
        "custom",
        "Create and edit saved reports",
        {"admin": True, "manager": True, "data_entry": True},
    ),
    (
        "share.manage",
        "share",
        "manage",
        "Create public dashboard share links",
        {"admin": True, "manager": True},
    ),
    # --- audit ---
    ("audit.view", "audit", "view", "View audit history", {"admin": True, "manager": True}),
    # --- bulk import ---
    (
        "import.run",
        "import",
        "run",
        "Bulk import masters and transactions from Excel",
        {"admin": True, "manager": True},
    ),
    # --- administration (admin only) ---
    ("users.manage", "users", "manage", "Create, edit and deactivate users", {"admin": True}),
    ("permissions.manage", "permissions", "manage", "Configure role permissions", {"admin": True}),
    ("backup.manage", "backup", "manage", "Create, restore and delete backups", {"admin": True}),
    ("settings.manage", "settings", "manage", "Change application settings", {"admin": True}),
    ("alerts.manage", "alerts", "manage", "Clear the notification log", {"admin": True}),
]

# Never configurable: removing these would lock every role out of the app or
# break the fixed role model. Admin is immutable so an admin cannot demote
# themselves out of the system.
LOCKED = {
    "admin": {
        "users.manage",
        "permissions.manage",
        "backup.manage",
        "settings.manage",
        "alerts.manage",
        "masters.view",
        "reports.view",
    },
    "viewer": {
        "masters.view",
        "sales.view",
        "purchases.view",
        "production.view",
        "inventory.view",
        "expenses.view",
        "employees.view",
        "reports.view",
    },
    "data_entry": {
        "masters.view",
        "masters.manage",
        "sales.view",
        "sales.manage",
        "purchases.view",
        "purchases.manage",
        "production.view",
        "production.manage",
        "inventory.view",
        "expenses.view",
        "expenses.manage",
        "employees.view",
        "employees.manage",
        "reports.view",
    },
    "manager": {
        "masters.view",
        "purchases.view",
        "sales.view",
        "production.view",
        "inventory.view",
        "audit.view",
        "reports.view",
    },
}


def default_allowed(permission_name: str, role: str) -> bool:
    for name, _module, _action, _desc, defaults in PERMISSIONS:
        if name == permission_name:
            return bool(defaults.get(role, False))
    return False


def is_locked(permission_name: str, role: str) -> bool:
    if role == "challan_entry":
        # The middleware allowlist below is the enforcement for this role, and
        # it is intentionally stricter than the matrix: only challan list and
        # create are meaningful toggles. Lock everything else so an Admin
        # choice cannot imply access the middleware will still refuse.
        return permission_name not in ("sales.view", "sales.manage")
    return permission_name in LOCKED.get(role, set())


def effective(permission_name: str, role: str, overrides: dict) -> bool:
    """Explicit override wins, otherwise the built-in default applies."""
    if permission_name in overrides:
        return bool(overrides[permission_name])
    return default_allowed(permission_name, role)


def load_overrides(db, role: str) -> dict:
    """{permission_name: granted} for explicit Admin choices only."""
    rows = db.query(RolePermission).filter(RolePermission.role == role).all()
    return {r.permission: bool(r.granted) for r in rows}


def resolve(db, role: str, permission_name: str) -> bool:
    if role == "admin" and permission_name in LOCKED["admin"]:
        return True
    return effective(permission_name, role, load_overrides(db, role))
