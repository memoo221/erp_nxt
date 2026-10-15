# Runs once, when a database is upgraded to 20.0.1.3.0.
#
# From this version, the Unit Cost of a client PO line (used by Odoo's Sale
# Margin app) is the average price of its confirmed purchases. Lines that were
# already bought keep the old product cost until something on them changes,
# so the new cost is written here once; their margin follows automatically.
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env["sale.order.line"].search(
        [("order_id.is_client_po", "=", True), ("qty_obtained", ">", 0)]
    )
    for line in lines:
        line.purchase_price = line.avg_purchase_price
