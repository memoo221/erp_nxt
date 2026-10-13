from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class ProcurementQuote(models.Model):
    """One request to ONE vendor to quote ONE item of a client PO.

    The same item can be sent to several vendors (several quote records for one
    sale order line), and one vendor can be asked for several items (several
    quote records with the same vendor).

    Life cycle:  requested -> quoted -> chosen
                                    \\-> rejected
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
    client_line_no = fields.Integer(
        related="sale_line_id.client_line_no", string="S. No.", store=True
    )
    client_item_code = fields.Char(related="sale_line_id.client_item_code")
    product_id = fields.Many2one(related="sale_line_id.product_id")
    item_description = fields.Text(related="sale_line_id.label", string="Description")
    product_uom_qty = fields.Float(
        related="sale_line_id.product_uom_qty", string="Qty Ordered"
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
    quoted_price = fields.Monetary(string="Quoted Unit Price")
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

    # The quoted price converted to the client PO's currency, so that quotes in
    # different currencies can be compared on one scale.
    price_order_currency = fields.Monetary(
        string="Price (PO Currency)",
        currency_field="order_currency_id",
        compute="_compute_price_order_currency",
        store=True,
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
    @api.constrains("state", "sale_line_id")
    def _check_single_chosen(self):
        """At most one chosen quote per item."""
        for line in self.sale_line_id:
            chosen = line.quote_ids.filtered(lambda q: q.state == "chosen")
            if len(chosen) > 1:
                raise ValidationError(
                    self.env._(
                        "Item %(item)s already has a chosen vendor (%(vendors)s). "
                        "Only one quote can be chosen per item.",
                        item=line.display_name,
                        vendors=", ".join(chosen.vendor_id.mapped("name")),
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
        if vals.get("quoted_price") and "state" not in vals:
            requested = self.filtered(lambda q: q.state == "requested")
            if requested:
                quoted_vals = dict(vals)
                self._apply_quoted_state(quoted_vals, current_state="requested")
                super(ProcurementQuote, requested).write(quoted_vals)
                return super(ProcurementQuote, self - requested).write(vals)
        return super().write(vals)

    @api.model
    def _apply_quoted_state(self, vals, current_state):
        if vals.get("quoted_price") and current_state == "requested":
            vals["state"] = "quoted"
            vals.setdefault("quote_date", fields.Date.context_today(self))

    # ------------------------------------------------------------------
    # Buttons (type="object" buttons in views call these methods)
    # ------------------------------------------------------------------
    def action_choose(self):
        """Choose this quote; reject every other quote of the same item."""
        for quote in self:
            if not quote.quoted_price:
                raise UserError(
                    self.env._("Enter the vendor's quoted price before choosing this quote.")
                )
            others = quote.sale_line_id.quote_ids - quote
            # Order matters: reject the others first, so that there is never a
            # moment with two chosen quotes (the constraint would refuse it).
            others.write({"state": "rejected"})
            quote.write({"state": "chosen"})
        return True

    def action_reset(self):
        """Undo a choice: every priced quote of the item is open again."""
        for quote in self:
            siblings = quote.sale_line_id.quote_ids
            siblings.filtered(lambda q: q.quoted_price).write({"state": "quoted"})
            siblings.filtered(lambda q: not q.quoted_price).write({"state": "requested"})
        return True
