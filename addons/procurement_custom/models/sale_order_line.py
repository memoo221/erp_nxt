from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_is_zero

# How far a printed amount may be from Odoo's computed amount and still count
# as "matching" (covers rounding differences between the two systems).
TOTAL_TOLERANCE = 0.01


def amounts_match(printed, computed, tolerance=TOTAL_TOLERANCE):
    """True when the two amounts differ by at most `tolerance`."""
    # Compare at 4 decimals so that a difference of exactly 0.01 (stored in
    # binary as 0.010000000000005...) is still accepted.
    return float_compare(abs(printed - computed), tolerance, precision_digits=4) <= 0


class SaleOrderLine(models.Model):
    # _inherit WITHOUT _name = extend the existing model in place: our fields
    # become new columns of the existing table sale_order_line, and our methods
    # are merged into the same class. Nothing in Odoo's own files is edited.
    _inherit = "sale.order.line"

    # Fields searched when the user types in a dropdown of order lines.
    # Odoo's default only covers the extra description and the order number;
    # we add the client's item code, the printed description and the product.
    _rec_names_search = (
        "name",
        "order_id.name",
        "client_item_code",
        "description_printed",
        "product_id",
    )

    # ------------------------------------------------------------------
    # The line as printed on the client's PO
    # ------------------------------------------------------------------
    client_line_no = fields.Integer(
        string="S. No.",
        help="Serial number of the line on the client's PO. Independent of "
        "Odoo's own ordering field (sequence).",
    )
    client_item_code = fields.Char(
        string="Client Item Code",
        help="Item code exactly as printed on the client's PO. A snapshot: it "
        "does not change if the product's internal reference changes later.",
    )
    description_printed = fields.Char(string="Description (as printed)")
    uom_printed = fields.Char(string="UoM (as printed)")
    total_printed = fields.Monetary(string="Total (as printed)")
    line_total_match = fields.Boolean(
        string="Total Matches",
        compute="_compute_line_total_match",
        store=True,
        help="The printed line total equals quantity x unit price (within 0.01).",
    )
    delivery_group_id = fields.Many2one(
        comodel_name="procurement.delivery.group",
        string="Delivery Group",
        # A domain filters the records offered in the dropdown. It is evaluated
        # in the browser with the current record's values.
        domain="[('order_id', '=', order_id)]",
        ondelete="set null",
        index="btree_not_null",
        # Not copied when an order is duplicated: the copy would point at the
        # ORIGINAL order's groups, which the constraint below forbids.
        copy=False,
    )

    # ------------------------------------------------------------------
    # Sourcing: quotes and purchases of this item
    # ------------------------------------------------------------------
    quote_ids = fields.One2many(
        comodel_name="procurement.quote", inverse_name="sale_line_id", string="Vendor Quotes"
    )
    purchase_ids = fields.One2many(
        comodel_name="procurement.purchase", inverse_name="sale_line_id", string="Purchases"
    )
    chosen_vendor_id = fields.Many2one(
        comodel_name="res.partner",
        string="Chosen Vendor",
        compute="_compute_chosen_vendor_id",
        store=True,
    )
    qty_obtained = fields.Float(
        string="Qty Obtained",
        digits="Product Unit",
        compute="_compute_procurement",
        store=True,
    )
    qty_remaining = fields.Float(
        string="Qty Remaining",
        digits="Product Unit",
        compute="_compute_procurement",
        store=True,
    )
    avg_purchase_price = fields.Float(
        string="Avg. Purchase Price",
        digits="Product Price",
        compute="_compute_procurement",
        store=True,
        help="Quantity-weighted average unit cost of the purchases, in the "
        "client PO's currency.",
    )
    margin_amount = fields.Monetary(
        string="Procurement Margin",
        compute="_compute_procurement",
        store=True,
        help="(client unit price - average purchase price) x quantity obtained, "
        "in the client PO's currency. Covers only what has been bought so far.",
    )
    procurement_status = fields.Selection(
        selection=[
            ("not_quoted", "Not quoted"),
            ("quoted", "Quoted"),
            ("vendor_chosen", "Vendor chosen"),
            ("partially_obtained", "Partially obtained"),
            ("obtained", "Obtained"),
        ],
        string="Procurement Status",
        compute="_compute_procurement",
        store=True,
    )

    # A UNIQUE INDEX with a WHERE clause ("partial index"): the pair
    # (order, S. No.) must be unique, but only for lines that have a number.
    # Section/note lines and ordinary sales orders keep 0 and are not affected.
    _client_line_no_unique = models.UniqueIndex(
        "(order_id, client_line_no) WHERE client_line_no > 0",
        "The S. No. must be unique within a client PO.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    # display_name is the text shown for a record in dropdowns and tags.
    # Odoo's default for an order line is "S00030 - [CODE] product name".
    # In the procurement screens the order is already known, so there we show
    # "7. ANNA97761N 2.167 Fuel-Filter-FF1191" instead.
    #
    # The switch is a CONTEXT key: the views that want the short label set
    # context="{'procurement_item_name': True}" on the field.
    # @api.depends_context tells Odoo the result differs per context value,
    # so it must not reuse a label cached for the other case.
    @api.depends_context("procurement_item_name")
    @api.depends("client_line_no", "client_item_code", "description_printed")
    def _compute_display_name(self):
        super()._compute_display_name()
        if not self.env.context.get("procurement_item_name"):
            return
        for line in self:
            description = line.description_printed or (line.label or "").split("\n", 1)[0]
            parts = [
                f"{line.client_line_no}." if line.client_line_no else "",
                line.client_item_code or "",
                description,
            ]
            line.display_name = " ".join(part for part in parts if part)

    @api.depends("total_printed", "price_subtotal")
    def _compute_line_total_match(self):
        for line in self:
            line.line_total_match = amounts_match(line.total_printed, line.price_subtotal)

    @api.depends("quote_ids.state", "quote_ids.vendor_id")
    def _compute_chosen_vendor_id(self):
        for line in self:
            chosen = line.quote_ids.filtered(lambda q: q.state == "chosen")[:1]
            line.chosen_vendor_id = chosen.vendor_id

    # One method may compute several fields. Odoo calls it when ANY dependency
    # changes: a purchase is added/edited/deleted, a quote changes state, or
    # the ordered quantity / client price of the line itself changes.
    @api.depends(
        "display_type",
        "product_uom_qty",
        "price_subtotal",
        "quote_ids.state",
        "purchase_ids.quantity",
        "purchase_ids.purchase_price",
        "purchase_ids.currency_id",
        "purchase_ids.date",
    )
    def _compute_procurement(self):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        for line in self:
            if line.display_type:  # a section or a note, not an item
                line.qty_obtained = line.qty_remaining = 0.0
                line.avg_purchase_price = line.margin_amount = 0.0
                line.procurement_status = False
                continue

            qty_obtained = sum(line.purchase_ids.mapped("quantity"))
            # Total cost in the PO's currency. A purchase paid in another
            # currency is converted at the rate of its own purchase date.
            total_cost = sum(
                purchase.quantity * line._to_order_currency(
                    purchase.purchase_price, purchase.currency_id, purchase.date
                )
                for purchase in line.purchase_ids
            )
            avg_price = total_cost / qty_obtained if qty_obtained else 0.0
            # Client unit price after discount, before tax.
            client_unit_price = (
                line.price_subtotal / line.product_uom_qty if line.product_uom_qty else 0.0
            )

            line.qty_obtained = qty_obtained
            line.qty_remaining = max(line.product_uom_qty - qty_obtained, 0.0)
            line.avg_purchase_price = avg_price
            line.margin_amount = (client_unit_price - avg_price) * qty_obtained

            # Status: the furthest step reached wins.
            quote_states = set(line.quote_ids.mapped("state"))
            if (
                not float_is_zero(qty_obtained, precision_digits=precision)
                and float_compare(qty_obtained, line.product_uom_qty, precision_digits=precision) >= 0
            ):
                line.procurement_status = "obtained"
            elif not float_is_zero(qty_obtained, precision_digits=precision):
                line.procurement_status = "partially_obtained"
            elif "chosen" in quote_states:
                line.procurement_status = "vendor_chosen"
            elif quote_states - {"requested"}:
                line.procurement_status = "quoted"
            else:
                line.procurement_status = "not_quoted"

    def _to_order_currency(self, amount, currency, date):
        """Convert a unit price to this line's (the client PO's) currency."""
        self.ensure_one()  # raises if `self` is not exactly one record
        if not currency or not self.currency_id or currency == self.currency_id:
            return amount
        return currency._convert(
            amount,
            self.currency_id,
            company=self.company_id,
            date=date or fields.Date.context_today(self),
            round=False,
        )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("delivery_group_id", "order_id")
    def _check_delivery_group_order(self):
        # The dropdown's domain only guides the user; this is the real rule.
        for line in self:
            if line.delivery_group_id and line.delivery_group_id.order_id != line.order_id:
                raise ValidationError(
                    self.env._(
                        "Delivery group %(group)s belongs to another client PO.",
                        group=line.delivery_group_id.name,
                    )
                )
