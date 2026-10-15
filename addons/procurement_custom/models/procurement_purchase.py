from odoo import api, fields, models


class ProcurementPurchase(models.Model):
    """A quantity of one PO item that we buy from a vendor.

    One item can have several purchase records: quantities often arrive in
    parts over time, sometimes at different prices, sometimes from different
    vendors. The totals per item (quantity obtained, average price, margin)
    are computed on sale.order.line, from CONFIRMED purchases only.

    Life cycle:  draft -> confirmed
    Choosing a quote creates a draft purchase; the user checks it and confirms.
    """

    _name = "procurement.purchase"
    _description = "Item Purchase"
    _order = "date desc, id desc"

    state = fields.Selection(
        selection=[("draft", "To Confirm"), ("confirmed", "Confirmed")],
        string="Status",
        default="draft",
        required=True,
        index=True,
        copy=False,
    )
    quote_id = fields.Many2one(
        comodel_name="procurement.quote",
        string="Chosen Quote",
        readonly=True,
        ondelete="set null",
        index="btree_not_null",
        help="The quote whose choice created this purchase.",
    )

    sale_line_id = fields.Many2one(
        comodel_name="sale.order.line",
        string="Item",
        required=True,
        ondelete="cascade",
        index=True,
        domain="[('display_type', '=', False)]",
    )
    order_id = fields.Many2one(
        related="sale_line_id.order_id", string="Client PO", store=True, index=True
    )
    client_line_no = fields.Integer(
        related="sale_line_id.client_line_no", string="S. No.", store=True, aggregator=None
    )
    client_item_code = fields.Char(related="sale_line_id.client_item_code")
    product_uom_id = fields.Many2one(related="sale_line_id.product_uom_id", string="UoM")
    qty_ordered = fields.Float(
        related="sale_line_id.product_uom_qty", string="Qty Ordered", aggregator=None
    )

    # The next three fields use the same pattern: compute + store +
    # readonly=False. When the item is picked, Odoo proposes the values of the
    # chosen quote; the user can overwrite them (the price really paid often
    # differs from the quote). This replaces the older `onchange` technique and
    # also works when records are created from code or by import.
    #
    # Each field has its OWN compute method on purpose. Fields that share one
    # method are treated as a unit: if a caller supplies just one of them,
    # Odoo skips the method entirely and the others would stay empty.
    vendor_id = fields.Many2one(
        comodel_name="res.partner",
        string="Vendor",
        required=True,
        domain="[('supplier_rank', '>', 0)]",
        compute="_compute_vendor_id",
        store=True,
        readonly=False,
        precompute=True,
    )
    purchase_price = fields.Monetary(
        string="Purchase Unit Price",
        aggregator="avg",
        compute="_compute_purchase_price",
        store=True,
        readonly=False,
        precompute=True,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        required=True,
        compute="_compute_currency_id",
        store=True,
        readonly=False,
        precompute=True,
    )
    qty_received = fields.Float(
        string="Qty Received",
        digits="Product Unit",
        default=0.0,
        help="The quantity of this purchase that has been received from the vendor.",
    )
    quantity = fields.Float(digits="Product Unit", required=True, default=1.0)
    date = fields.Date(required=True, default=fields.Date.context_today)
    notes = fields.Char()
    amount = fields.Monetary(string="Total Cost", compute="_compute_amount", store=True)

    _quantity_positive = models.Constraint(
        "CHECK(quantity > 0)", "The purchased quantity must be greater than zero."
    )
    _price_not_negative = models.Constraint(
        "CHECK(purchase_price >= 0)", "The purchase price cannot be negative."
    )

    def _chosen_quote(self):
        """The quote this purchase came from, else the item's first chosen quote."""
        self.ensure_one()
        return self.quote_id or self.sale_line_id.quote_ids.filtered(
            lambda q: q.state == "chosen"
        )[:1]

    # In the three methods below a value is only filled in while the field is
    # still empty, so changing the item never wipes what the user typed.
    @api.depends("sale_line_id")
    def _compute_vendor_id(self):
        for purchase in self:
            if not purchase.vendor_id:
                purchase.vendor_id = purchase._chosen_quote().vendor_id

    @api.depends("sale_line_id")
    def _compute_purchase_price(self):
        for purchase in self:
            if not purchase.purchase_price:
                purchase.purchase_price = purchase._chosen_quote().quoted_price

    @api.depends("sale_line_id")
    def _compute_currency_id(self):
        for purchase in self:
            if not purchase.currency_id:
                purchase.currency_id = (
                    purchase._chosen_quote().currency_id
                    or purchase.sale_line_id.currency_id
                    or self.env.company.currency_id
                )

    @api.depends("quantity", "purchase_price")
    def _compute_amount(self):
        for purchase in self:
            purchase.amount = purchase.quantity * purchase.purchase_price

    @api.depends("vendor_id", "sale_line_id", "quantity")
    def _compute_display_name(self):
        for purchase in self:
            purchase.display_name = (
                f"{purchase.quantity:g} x {purchase.sale_line_id.display_name or ''}"
                f" ({purchase.vendor_id.name or ''})"
            )

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def action_confirm(self):
        """The purchase is done: its quantity now counts as obtained."""
        self.write({"state": "confirmed"})
        return True

    def action_draft(self):
        self.write({"state": "draft"})
        return True
