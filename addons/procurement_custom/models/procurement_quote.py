from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


class ProcurementQuote(models.Model):
    """One request to ONE vendor to quote ONE item of a client PO.

    The same item can be sent to several vendors (several quote records for one
    sale order line), and one vendor can be asked for several items (several
    quote records with the same vendor).

    Life cycle:  requested -> quoted -> chosen
                                    \\-> rejected

    An item may be split between vendors: several quotes of the same item can
    be chosen, each for its own "Qty to Buy", as long as together they do not
    exceed the ordered quantity. Choose creates a DRAFT purchase for that
    quantity; confirming the purchase is what counts it as obtained.
    """

    _name = "procurement.quote"
    _description = "Vendor Quote"
    _order = "order_id desc, client_line_no, quoted_price, id"

    sale_line_id = fields.Many2one(
        comodel_name="sale.order.line",
        string="Item",
        required=True,
        ondelete="cascade",
        index=True,
        # Sections and notes are also sale.order.line rows; they are not items.
        domain="[('display_type', '=', False)]",
    )
    # Related fields read their value through a relation. With store=True the
    # value is also copied into a column of THIS table, which is what makes
    # "group by client PO" and "sort by line number" possible in list views.
    order_id = fields.Many2one(
        related="sale_line_id.order_id", string="Client PO", store=True, index=True
    )
    # aggregator: what a grouped list shows in the group's summary row.
    # Numbers are summed by default, which is meaningless for a line number
    # or for a quantity repeated on every quote, so it is switched off.
    client_line_no = fields.Integer(
        related="sale_line_id.client_line_no", string="S. No.", store=True, aggregator=None
    )
    client_item_code = fields.Char(related="sale_line_id.client_item_code")
    product_id = fields.Many2one(related="sale_line_id.product_id")
    item_description = fields.Text(related="sale_line_id.label", string="Description")
    product_uom_qty = fields.Float(
        related="sale_line_id.product_uom_qty", string="Qty Ordered", aggregator=None
    )
    qty_needed = fields.Float(
        string="Qty Needed",
        digits="Product Unit",
        compute="_compute_qty_needed",
        aggregator=None,
        help="Ordered quantity of the item not yet assigned to a chosen vendor.",
    )
    product_uom_id = fields.Many2one(related="sale_line_id.product_uom_id", string="UoM")

    vendor_id = fields.Many2one(
        comodel_name="res.partner",
        string="Vendor",
        required=True,
        index=True,
        # supplier_rank > 0 is how Odoo marks a contact as a vendor.
        domain="[('supplier_rank', '>', 0)]",
    )
    state = fields.Selection(
        selection=[
            ("requested", "Requested"),
            ("quoted", "Quoted"),
            ("chosen", "Chosen"),
            ("rejected", "Rejected"),
        ],
        default="requested",
        required=True,
        index=True,
    )
    # A Monetary field is a number shown with a currency symbol. It needs a
    # companion Many2one to res.currency; by convention named `currency_id`.
    # aggregator="min": the summary row of an item shows its LOWEST quote.
    quoted_price = fields.Monetary(string="Quoted Unit Price", aggregator="min")
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        required=True,
        # compute + store + readonly=False = a "smart default": filled in from
        # the client PO's currency, but the user may change it (a vendor can
        # quote in EGP for a PO that is in USD).
        compute="_compute_currency_id",
        store=True,
        readonly=False,
        precompute=True,
    )
    quote_date = fields.Date()
    notes = fields.Char()

    qty_to_buy = fields.Float(
        string="Qty to Buy",
        digits="Product Unit",
        # Each quote has its own quantity: summing them per item makes sense.
        help="How many units to buy from this vendor. Type it on the quote row "
        "before pressing Choose; left empty, Choose takes everything not yet "
        "assigned to another vendor.",
    )
    amount_to_buy = fields.Monetary(
        string="Total",
        compute="_compute_amount_to_buy",
        store=True,
        # Quotes of one item may be in different currencies: no group sum.
        aggregator=None,
        help="Qty to Buy x Quoted Unit Price, in the quote's currency.",
    )
    purchase_ids = fields.One2many(
        comodel_name="procurement.purchase", inverse_name="quote_id", string="Purchases"
    )
    # The quoted price converted to the client PO's currency, so that quotes in
    # different currencies can be compared on one scale.
    price_order_currency = fields.Monetary(
        string="Price (PO Currency)",
        currency_field="order_currency_id",
        compute="_compute_price_order_currency",
        store=True,
        aggregator="min",
    )
    order_currency_id = fields.Many2one(
        related="sale_line_id.currency_id", string="PO Currency"
    )
    is_lowest = fields.Boolean(
        string="Lowest Price",
        compute="_compute_is_lowest",
        store=True,
        help="This is the cheapest priced quote for the item.",
    )

    _line_vendor_unique = models.Constraint(
        "UNIQUE(sale_line_id, vendor_id)",
        "This vendor has already been asked to quote this item.",
    )
    _qty_to_buy_not_negative = models.Constraint(
        "CHECK(qty_to_buy >= 0)", "The quantity to buy cannot be negative."
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("sale_line_id")
    def _compute_currency_id(self):
        for quote in self:
            if not quote.currency_id:
                quote.currency_id = (
                    quote.sale_line_id.currency_id or self.env.company.currency_id
                )

    @api.depends("quoted_price", "currency_id", "quote_date", "sale_line_id.currency_id")
    def _compute_price_order_currency(self):
        for quote in self:
            order_currency = quote.sale_line_id.currency_id
            if quote.currency_id and order_currency and quote.currency_id != order_currency:
                # _convert uses the exchange rates stored in Odoo (Accounting >
                # Currencies) for the given date. round=False keeps all the
                # decimals: unit prices on these POs go down to 6 places.
                quote.price_order_currency = quote.currency_id._convert(
                    quote.quoted_price,
                    order_currency,
                    company=quote.sale_line_id.company_id,
                    date=quote.quote_date or fields.Date.context_today(quote),
                    round=False,
                )
            else:
                quote.price_order_currency = quote.quoted_price

    # This compute looks at the SIBLING quotes of the same item. The dependency
    # "sale_line_id.quote_ids.xxx" means: when any quote of the item changes its
    # price or state, recompute all quotes of that item.
    @api.depends(
        "state",
        "price_order_currency",
        "sale_line_id.quote_ids.state",
        "sale_line_id.quote_ids.price_order_currency",
    )
    def _compute_is_lowest(self):
        for quote in self:
            priced = quote.sale_line_id.quote_ids.filtered(
                lambda q: q.state != "requested" and q.price_order_currency > 0
            )
            lowest = min(priced.mapped("price_order_currency"), default=0.0)
            quote.is_lowest = quote in priced and quote.price_order_currency == lowest

    # Same value on every quote of an item: what the chosen vendors leave over.
    @api.depends(
        "sale_line_id.product_uom_qty",
        "sale_line_id.quote_ids.state",
        "sale_line_id.quote_ids.qty_to_buy",
    )
    def _compute_qty_needed(self):
        for quote in self:
            chosen = quote.sale_line_id.quote_ids.filtered(lambda q: q.state == "chosen")
            quote.qty_needed = max(
                quote.sale_line_id.product_uom_qty - sum(chosen.mapped("qty_to_buy")), 0.0
            )

    @api.depends("qty_to_buy", "quoted_price")
    def _compute_amount_to_buy(self):
        for quote in self:
            quote.amount_to_buy = quote.qty_to_buy * quote.quoted_price

    @api.depends("vendor_id", "sale_line_id")
    def _compute_display_name(self):
        # display_name is what Odoo shows wherever a record is referenced.
        for quote in self:
            quote.display_name = (
                f"{quote.vendor_id.name or ''} / {quote.sale_line_id.display_name or ''}"
            )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("state", "qty_to_buy", "sale_line_id")
    def _check_chosen_quantity(self):
        """The chosen vendors together may not cover more than was ordered."""
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        for line in self.sale_line_id:
            chosen = line.quote_ids.filtered(lambda q: q.state == "chosen")
            allocated = sum(chosen.mapped("qty_to_buy"))
            if float_compare(allocated, line.product_uom_qty, precision_digits=precision) > 0:
                raise ValidationError(
                    self.env._(
                        "Item %(item)s: the chosen vendors (%(vendors)s) add up to "
                        "%(allocated)s, but only %(ordered)s were ordered.",
                        item=line.display_name,
                        vendors=", ".join(chosen.vendor_id.mapped("name")),
                        allocated=allocated,
                        ordered=line.product_uom_qty,
                    )
                )

    # ------------------------------------------------------------------
    # create / write overrides
    # ------------------------------------------------------------------
    # Typing a price is what turns a "requested" quote into a "quoted" one.
    # Doing it here (instead of in the view) guarantees the rule whichever way
    # the record is written: form, list, import, or code.
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_quoted_state(vals, current_state=vals.get("state", "requested"))
        # super() calls the next create() in the inheritance chain, ending in
        # the ORM's own implementation that performs the INSERT.
        return super().create(vals_list)

    def write(self, vals):
        chosen = self.filtered(lambda q: q.state == "chosen")
        if "qty_to_buy" in vals and "state" not in vals and chosen:
            # A chosen quantity may be changed only while its purchase is
            # still a draft: a confirmed purchase is a done deal.
            if chosen.purchase_ids.filtered(lambda p: p.state == "confirmed"):
                raise UserError(
                    self.env._(
                        "The purchase for this vendor is already confirmed. Set it "
                        "back to draft in Purchases before changing the quantity."
                    )
                )
            if not vals["qty_to_buy"]:
                raise UserError(
                    self.env._("To drop a chosen vendor, press Undo instead of clearing the quantity.")
                )

        if vals.get("quoted_price") and "state" not in vals:
            requested = self.filtered(lambda q: q.state == "requested")
            if requested:
                quoted_vals = dict(vals)
                self._apply_quoted_state(quoted_vals, current_state="requested")
                super(ProcurementQuote, requested).write(quoted_vals)
                res = super(ProcurementQuote, self - requested).write(vals)
            else:
                res = super().write(vals)
        else:
            res = super().write(vals)

        if "qty_to_buy" in vals and "state" not in vals and chosen:
            # The draft purchase follows the new quantity, and the other
            # vendors reopen (or close) depending on what is left.
            for quote in chosen:
                quote.purchase_ids.filtered(lambda p: p.state == "draft").write(
                    {"quantity": quote.qty_to_buy}
                )
            chosen.sale_line_id._update_open_quotes()
        return res

    @api.model
    def _apply_quoted_state(self, vals, current_state):
        if vals.get("quoted_price") and current_state == "requested":
            vals["state"] = "quoted"
            vals.setdefault("quote_date", fields.Date.context_today(self))

    # ------------------------------------------------------------------
    # Buttons (type="object" buttons in views call these methods)
    # ------------------------------------------------------------------
    def _qty_unassigned(self):
        """Ordered quantity of the item not yet assigned to another chosen quote."""
        self.ensure_one()
        others = self.sale_line_id.quote_ids.filtered(
            lambda q: q.state == "chosen" and q != self
        )
        return self.sale_line_id.product_uom_qty - sum(others.mapped("qty_to_buy"))

    def _check_can_choose(self):
        self.ensure_one()
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        if not self.quoted_price:
            raise UserError(
                self.env._("Enter the vendor's quoted price before choosing this quote.")
            )
        if float_compare(self._qty_unassigned(), 0.0, precision_digits=precision) <= 0:
            raise UserError(
                self.env._(
                    "The whole ordered quantity of %(item)s is already assigned to "
                    "other vendors. Undo one of those choices first.",
                    item=self.sale_line_id.display_name,
                )
            )

    def action_add(self):
        """Save button of a new quote: back to its PO's comparison table.

        The web client saves the record before calling a button method, so
        there is nothing left to do here but open the table.
        """
        self.ensure_one()
        action = self.order_id.action_view_quotes()
        # "main" replaces the breadcrumbs instead of stacking the table on
        # top of the form it came from.
        action["target"] = "main"
        return action

    def action_choose(self):
        """Choose this vendor for its Qty to Buy (empty = what is left)."""
        for quote in self:
            quote._check_can_choose()
            quote._choose(quote.qty_to_buy or quote._qty_unassigned())
        return True

    def _choose(self, quantity):
        """Choose this vendor for `quantity` units and create a draft purchase.

        When the chosen quotes of the item cover the whole ordered quantity,
        the item's remaining open quotes are rejected. Returns the purchase.
        """
        self.ensure_one()
        self._check_can_choose()
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        if float_compare(quantity, 0.0, precision_digits=precision) <= 0:
            raise UserError(self.env._("Enter a quantity greater than zero."))
        # Writing "chosen" runs _check_chosen_quantity, which refuses a
        # quantity larger than what is left.
        self.write({"qty_to_buy": quantity, "state": "chosen"})
        purchase = self.env["procurement.purchase"].create(
            {
                "quote_id": self.id,
                "sale_line_id": self.sale_line_id.id,
                "vendor_id": self.vendor_id.id,
                "quantity": quantity,
                "purchase_price": self.quoted_price,
                "currency_id": self.currency_id.id,
            }
        )
        self.sale_line_id._update_open_quotes()
        return purchase

    def action_reset(self):
        """Undo a choice: delete its draft purchase and reopen rejected quotes."""
        for quote in self:
            if quote.purchase_ids.filtered(lambda p: p.state == "confirmed"):
                raise UserError(
                    self.env._(
                        "The purchase from %(vendor)s is already confirmed. Set it "
                        "back to draft before undoing the choice.",
                        vendor=quote.vendor_id.name,
                    )
                )
            # Sales users may not delete purchases in general; removing the
            # unconfirmed purchase this choice created is part of undoing it.
            quote.purchase_ids.sudo().unlink()
            quote._reopen()
            quote.sale_line_id._update_open_quotes()
        return True

    def _reopen(self):
        """Back to "quoted" if a price is known, else "requested"."""
        self.filtered(lambda q: q.quoted_price).write({"state": "quoted"})
        self.filtered(lambda q: not q.quoted_price).write({"state": "requested"})
