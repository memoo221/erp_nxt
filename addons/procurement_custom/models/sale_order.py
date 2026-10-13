from odoo import api, fields, models
from odoo.exceptions import UserError

from .sale_order_line import amounts_match


class SaleOrder(models.Model):
    """The client's purchase order, stored as an Odoo sales order.

    From the client's side the document is a PURCHASE order; from ours it is a
    SALE (we are the supplier), which is why it lives on sale.order.

    Design rule: nothing printed on the client's PDF may be lost.
      * `*_printed` fields hold text or amounts exactly as printed, so the
        record can always be compared word for word with the paper.
      * The other fields hold the same information in structured form (dates,
        numbers, links) when the system needs to calculate or search with it.
    """

    _inherit = "sale.order"

    is_client_po = fields.Boolean(
        string="Client PO",
        help="This sales order is a purchase order received from a client. "
        "Set automatically for orders created from the Procurement menu.",
    )

    # ------------------------------------------------------------------
    # Client letterhead (snapshot: the contact record may change later)
    # ------------------------------------------------------------------
    client_name_printed = fields.Char(string="Client Name (as printed)")
    client_address_printed = fields.Text(string="Client Address (as printed)")
    client_po_box = fields.Char(string="P.O. Box")
    client_phone_printed = fields.Char(string="Phone (as printed)")
    client_fax_printed = fields.Char(string="Fax (as printed)")

    # ------------------------------------------------------------------
    # Client PO header
    # ------------------------------------------------------------------
    # PO No.        -> client_order_ref  (existing Odoo field, reused)
    # Currency      -> currency_id       (existing, comes from the pricelist)
    # date_order    -> the date WE registered the PO, not the client's PO date
    client_po_date = fields.Date(string="PO Creation Date")
    client_po_approval_date = fields.Date(string="PO Approval Date")
    client_pr_number = fields.Char(string="PR No.")
    client_pr_approval_date = fields.Date(string="PR Approval Date")
    client_buyer_id = fields.Many2one(
        comodel_name="res.partner",
        string="Buyer",
        domain="[('parent_id', '=', partner_id)]",
        help="The client's buyer, as a contact under the client company.",
    )
    client_buyer_name_printed = fields.Char(string="Buyer (as printed)")
    client_requester_id = fields.Many2one(
        comodel_name="res.partner",
        string="Requester",
        domain="[('parent_id', '=', partner_id)]",
    )
    client_requester_name_printed = fields.Char(string="Requester (as printed)")
    client_requester_dept = fields.Char(string="Requester Dept.")
    client_po_type = fields.Char(string="PO Type", help='e.g. "RFP 15/2025"')
    rfp_reference = fields.Char(string="RFP Reference")

    penalty_text_printed = fields.Char(string="Penalty (as printed)")
    penalty_rate_weekly = fields.Float(string="Penalty per Week (%)", digits=(5, 2))
    penalty_cap = fields.Float(string="Penalty Cap (%)", digits=(5, 2))
    penalty_base = fields.Selection(
        selection=[("total_po_value", "Total PO value")],
        string="Penalty Base",
    )

    # ------------------------------------------------------------------
    # Supplier block (how the client printed OUR details)
    # ------------------------------------------------------------------
    # Our company itself stays in company_id.
    supplier_name_printed = fields.Char(string="Supplier Name (as printed)")
    supplier_address_printed = fields.Text(
        string="Supplier Address (as printed)",
        help="Verbatim, even if the PDF repeats the address twice.",
    )

    # ------------------------------------------------------------------
    # Header terms (short values in the header table)
    # ------------------------------------------------------------------
    # payment_term_id, incoterm, partner_shipping_id are existing fields.
    payment_term_text_printed = fields.Char(
        string="Payment Term (as printed)", help='e.g. "As per below details"'
    )
    delivery_term_printed = fields.Char(string="Delivery Term (as printed)")
    delivery_time_printed = fields.Char(string="Delivery Time (as printed)")
    delivery_location_printed = fields.Char(string="Delivery Location (as printed)")

    # ------------------------------------------------------------------
    # Payment terms (page 2)
    # ------------------------------------------------------------------
    payment_terms_text_printed = fields.Text(string="Payment Terms (as printed)")
    # One2many: "all milestones whose order_id is this order". The list is
    # edited directly inside the order form.
    payment_milestone_ids = fields.One2many(
        comodel_name="procurement.payment.milestone",
        inverse_name="order_id",
        string="Payment Milestones",
        copy=True,
    )
    payment_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Payment Currency",
        help="Currency the client actually pays in (may differ from the PO currency).",
    )
    exchange_rate_rule = fields.Char(help='e.g. "CBE rate at payment time"')
    payment_counter_start = fields.Char(
        help='e.g. "submission date of an accepted invoice"'
    )
    lg_release_condition = fields.Char(string="LG Release Condition")
    partial_invoicing_allowed = fields.Boolean()
    pac_fac_applied = fields.Boolean(
        string="PAC/FAC Applied",
        help="Provisional / Final Acceptance Certificate required.",
    )
    advance_payment_received_date = fields.Date(
        help="Not printed on the PO: entered when the advance payment arrives. "
        "All delivery deadlines are counted from this date.",
    )

    # ------------------------------------------------------------------
    # Delivery terms (page 2)
    # ------------------------------------------------------------------
    delivery_terms_text_printed = fields.Text(string="Delivery Terms (as printed)")
    delivery_group_ids = fields.One2many(
        comodel_name="procurement.delivery.group",
        inverse_name="order_id",
        string="Delivery Groups",
    )
    delivery_destination_printed = fields.Char(
        string="Delivery Destination (as printed)",
        help='e.g. "DDP to Etisalat warehouse"',
    )

    # ------------------------------------------------------------------
    # Warranty (page 2)
    # ------------------------------------------------------------------
    warranty_text_printed = fields.Text(string="Warranty (as printed)")
    warranty_months = fields.Integer()
    warranty_start = fields.Selection(
        selection=[("delivery_date", "Delivery date")], string="Warranty Starts From"
    )
    warranty_scope = fields.Char(help='e.g. "manufacturing defects"')
    warranty_reference = fields.Char(help='e.g. "BOQ identification (Annex-I)"')

    # ------------------------------------------------------------------
    # Notes, approval and document details
    # ------------------------------------------------------------------
    notes_to_supplier_printed = fields.Text(string="Notes to Supplier (as printed)")
    annex_reference = fields.Char()
    approved_by_name = fields.Char(
        help="Leave empty when the signature is illegible."
    )
    approved_by_department = fields.Char(help="As shown on the client's stamp.")
    client_stamp_present = fields.Boolean()
    other_stamps_note = fields.Text(string="Other Stamps")
    handwritten_marks = fields.Char(help='e.g. "initials on each page"')
    invoice_submission_text_printed = fields.Text(
        string="Invoice Submission (as printed)",
        help="The footer line about receiving invoices and delivery of checks.",
    )
    invoice_submission_days = fields.Char(help='e.g. "Sun, Mon, Wed"')
    invoice_submission_location = fields.Char()
    # Hours stored as a decimal number: 13.5 = 13:30. The view shows it as a
    # time with widget="float_time".
    invoice_submission_hour_from = fields.Float(string="Submission From")
    invoice_submission_hour_to = fields.Float(string="Submission To")
    client_po_printed_datetime = fields.Datetime(string="Printed Date")
    client_po_page_count = fields.Integer(string="Page Count")
    client_po_document_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Scanned PO",
        # Only files attached to THIS order are offered.
        domain="[('res_model', '=', 'sale.order'), ('res_id', '=', id)]",
        help="The main scan of the client's PO. Attach the file to this order "
        "first (paperclip in the chatter), then select it here.",
    )

    # ------------------------------------------------------------------
    # Totals as printed, kept apart from Odoo's computed totals
    # ------------------------------------------------------------------
    total_po_amount_printed = fields.Monetary(string="Total PO Amount (as printed)")
    tax_table_printed = fields.Monetary(
        string="Tax Table (as printed)", help="A separate Egyptian tax line on the PO."
    )
    # A plain Float with 4 decimals: the PO prints the VAT unrounded
    # (e.g. 48835.8472), and a Monetary field would round it to 2 decimals.
    tax_vat_printed = fields.Float(string="Tax VAT (as printed)", digits=(16, 4))
    grand_total_printed = fields.Monetary(string="PO Grand Total (as printed)")
    grand_total_words_printed = fields.Char(string="Amount in Words (as printed)")
    totals_match = fields.Boolean(
        compute="_compute_totals_match",
        store=True,
        help="The printed totals equal Odoo's computed totals (within 0.01).",
    )
    lines_total_mismatch_count = fields.Integer(
        string="Lines with a Different Total",
        compute="_compute_lines_total_mismatch_count",
    )

    # ------------------------------------------------------------------
    # Procurement progress
    # ------------------------------------------------------------------
    item_count = fields.Integer(string="Items", compute="_compute_progress", store=True)
    items_chosen_count = fields.Integer(
        string="Items with Vendor", compute="_compute_progress", store=True
    )
    items_obtained_count = fields.Integer(
        string="Items Obtained", compute="_compute_progress", store=True
    )
    progress_percent = fields.Float(
        string="Progress (%)",
        compute="_compute_progress",
        store=True,
        help="Share of items fully obtained.",
    )
    quote_count = fields.Integer(compute="_compute_related_counts")
    purchase_count = fields.Integer(compute="_compute_related_counts")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends(
        "total_po_amount_printed",
        "tax_table_printed",
        "tax_vat_printed",
        "grand_total_printed",
        "amount_untaxed",
        "amount_tax",
        "amount_total",
    )
    def _compute_totals_match(self):
        for order in self:
            # Odoo has a single tax amount; the PO prints two tax lines.
            printed_tax = order.tax_table_printed + order.tax_vat_printed
            order.totals_match = (
                amounts_match(order.total_po_amount_printed, order.amount_untaxed)
                and amounts_match(printed_tax, order.amount_tax)
                and amounts_match(order.grand_total_printed, order.amount_total)
            )

    @api.depends("order_line.line_total_match", "order_line.display_type")
    def _compute_lines_total_mismatch_count(self):
        for order in self:
            order.lines_total_mismatch_count = len(
                order.order_line.filtered(
                    lambda l: not l.display_type and not l.line_total_match
                )
            )

    @api.depends(
        "order_line.display_type",
        "order_line.procurement_status",
        "order_line.chosen_vendor_id",
    )
    def _compute_progress(self):
        for order in self:
            items = order.order_line.filtered(lambda l: not l.display_type)
            obtained = items.filtered(lambda l: l.procurement_status == "obtained")
            order.item_count = len(items)
            order.items_chosen_count = len(items.filtered("chosen_vendor_id"))
            order.items_obtained_count = len(obtained)
            order.progress_percent = 100.0 * len(obtained) / len(items) if items else 0.0

    # Not stored: computed on the fly each time the form is opened.
    # _read_group runs ONE "SELECT order_id, count(*) ... GROUP BY order_id"
    # for all orders at once, instead of one query per order.
    def _compute_related_counts(self):
        for field_name, model in (
            ("quote_count", "procurement.quote"),
            ("purchase_count", "procurement.purchase"),
        ):
            counts = dict(
                self.env[model]._read_group(
                    domain=[("order_id", "in", self.ids)],
                    groupby=["order_id"],
                    aggregates=["__count"],
                )
            )
            for order in self:
                order[field_name] = counts.get(order, 0)

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    # A button method may return an "action": a dictionary that tells the web
    # client what to open next (a window, a report, a wizard...).
    def action_request_quotes(self):
        """Open the "Request quotes" wizard for this order."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Request Quotes"),
            "res_model": "procurement.quote.request.wizard",
            "view_mode": "form",
            "target": "new",  # open as a dialog on top of the order
            # default_<field> keys in the context pre-fill a new record.
            "context": {"default_order_id": self.id},
        }

    def action_view_quotes(self):
        """Open the comparison screen: this order's quotes grouped by item."""
        self.ensure_one()
        # Reuse the menu's action, narrowed down to this order.
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "procurement_custom.action_procurement_quote"
        )
        action["domain"] = [("order_id", "=", self.id)]
        # procurement_order_id: read by the views to limit the Item dropdown
        # to this PO when a row is added by hand.
        action["context"] = {"search_default_group_item": 1, "procurement_order_id": self.id}
        action["display_name"] = self.env._("Quote Comparison: %s", self.display_name)
        return action

    def action_view_purchases(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "procurement_custom.action_procurement_purchase"
        )
        action["domain"] = [("order_id", "=", self.id)]
        action["context"] = {"procurement_order_id": self.id}
        return action

    def action_print_quote_requests(self):
        """Print one request page per vendor for the quotes still open."""
        self.ensure_one()
        quotes = self.env["procurement.quote"].search(
            [("order_id", "=", self.id), ("state", "in", ("requested", "quoted"))]
        )
        if not quotes:
            raise UserError(
                self.env._("There are no open quote requests on this client PO.")
            )
        return self.env.ref(
            "procurement_custom.action_report_quote_request"
        ).report_action(quotes)
