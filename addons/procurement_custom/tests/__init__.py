# Odoo's test runner imports this package by itself when tests are enabled.
# It must NOT be imported from the module's main __init__.py.
from . import test_procurement
