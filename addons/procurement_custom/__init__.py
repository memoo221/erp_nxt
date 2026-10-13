# Python only runs the files that are imported. Odoo imports this package when
# the module is loaded, so every sub-package with models must be imported here.
# (The `tests` package is the exception: Odoo's test runner finds it itself.)
from . import models
from . import wizard
