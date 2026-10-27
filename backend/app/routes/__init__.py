# Route modules - imported in main.py to register all routes
# Order matters: static paths MUST be registered before parameterized ones
# (e.g., /import/undo before /import/{import_type})

from . import auth
from . import users
from . import dashboard
from . import customers
from . import suppliers
from . import raw_materials
from . import products
from . import production
from . import purchase
from . import sales
from . import employees
from . import reports
from . import settings
from . import backup
from . import import_export