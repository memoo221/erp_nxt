# Runs once, when a database is upgraded to 20.0.1.4.0.
#
# From this version a client PO line is invoiced for its obtained quantity
# instead of the ordered one. The stored "to invoice" quantity (and the
# invoice status that depends on it) of existing lines is recalculated once;
# afterwards Odoo keeps it up to date by itself.
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env["sale.order.line"].search([("order_id.is_client_po", "=", True)])
    Line = env["sale.order.line"]
    for field_name in ("qty_to_invoice", "invoice_status"):
        env.add_to_compute(Line._fields[field_name], lines)
        env.flush_all()
    orders = lines.order_id
    env.add_to_compute(env["sale.order"]._fields["invoice_status"], orders)
    env.flush_all()
