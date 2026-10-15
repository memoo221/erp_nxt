# Runs once, when a database is upgraded to 20.0.1.2.0.
#
# Quotes chosen before "Qty to Buy" existed have no quantity, although the
# choice covered the whole item. Give each one what the item's other chosen
# quotes leave over. When nothing is left (another vendor was chosen for the
# whole quantity later), the old choice is superseded and reopened.
# Written through the ORM so the stored Total is recomputed as well.
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    quotes = env["procurement.quote"].search([("state", "=", "chosen"), ("qty_to_buy", "=", 0)])
    for quote in quotes:
        left = quote._qty_unassigned()
        if left > 0:
            quote.qty_to_buy = left
        else:
            quote._reopen()
