from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command


class ProcurementQuoteRequestWizard(models.TransientModel):
    """Dialog: choose vendors and, per vendor, the items to ask a quote for.

    A WIZARD is a model based on TransientModel. Its records are real database
    rows, but temporary: they only hold what the user types in the dialog, and
    Odoo deletes them automatically after a while. The lasting result is what
    the confirm button creates (here: procurement.quote records).
    """

    _name = "procurement.quote.request.wizard"
    _description = "Request Vendor Quotes"

    order_id = fields.Many2one(
        comodel_name="sale.order", string="Client PO", required=True, readonly=True
    )
    line_ids = fields.One2many(
        comodel_name="procurement.quote.request.wizard.line",
        inverse_name="wizard_id",
        string="Vendors",
    )

    def action_create_requests(self):
        """Create one "requested" quote per (vendor, item) pair."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(self.env._("Add at least one vendor."))

        # Pairs that already exist are skipped, so the wizard can be run again
        # later to add vendors or items without creating duplicates.
        existing = {
            (quote.vendor_id.id, quote.sale_line_id.id)
            for quote in self.env["procurement.quote"].search(
                [("order_id", "=", self.order_id.id)]
            )
        }
        vals_list = []
        for wizard_line in self.line_ids:
            if not wizard_line.sale_line_ids:
                raise UserError(
                    self.env._(
                        "Select at least one item for vendor %s.", wizard_line.vendor_id.name
                    )
                )
            for sale_line in wizard_line.sale_line_ids:
                pair = (wizard_line.vendor_id.id, sale_line.id)
                if pair not in existing:
                    existing.add(pair)
                    vals_list.append(
                        {"vendor_id": wizard_line.vendor_id.id, "sale_line_id": sale_line.id}
                    )
        # create() accepts a list and inserts all records in one go.
        self.env["procurement.quote"].create(vals_list)

        # Close the dialog and show the order's quotes.
        return self.order_id.action_view_quotes()


class ProcurementQuoteRequestWizardLine(models.TransientModel):
    """One row of the dialog: a vendor and the items requested from it."""

    _name = "procurement.quote.request.wizard.line"
    _description = "Request Vendor Quotes: Vendor Line"

    wizard_id = fields.Many2one(
        comodel_name="procurement.quote.request.wizard", required=True, ondelete="cascade"
    )
    order_id = fields.Many2one(related="wizard_id.order_id")
    vendor_id = fields.Many2one(
        comodel_name="res.partner",
        string="Vendor",
        required=True,
        domain="[('supplier_rank', '>', 0)]",
    )
    # Many2many: a vendor row holds many items, and an item can be on many
    # vendor rows. Odoo stores the pairs in a separate link table.
    sale_line_ids = fields.Many2many(
        comodel_name="sale.order.line",
        string="Items",
        domain="[('order_id', '=', order_id), ('display_type', '=', False)]",
        # Pre-filled with every item of the order (see the method below); the
        # user removes the ones this vendor should not receive.
        compute="_compute_sale_line_ids",
        store=True,
        readonly=False,
    )

    @api.depends("wizard_id")
    def _compute_sale_line_ids(self):
        for wizard_line in self:
            if not wizard_line.sale_line_ids:
                items = wizard_line.order_id.order_line.filtered(
                    lambda l: not l.display_type
                )
                # Command.set replaces the whole content of an x2many field.
                wizard_line.sale_line_ids = [Command.set(items.ids)]
