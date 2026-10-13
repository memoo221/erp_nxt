from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class ProcurementDeliveryGroup(models.Model):
    """A group of PO lines that share one delivery rule.

    Example from a client PO: "Filters: a batch within 2 weeks, the rest
    within 1-4 months from the advance payment".
    """

    _name = "procurement.delivery.group"
    _description = "Client PO Delivery Group"
    _order = "order_id, sequence, id"

    order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Client PO",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Group", required=True)
    description = fields.Char()
    immediate_batch = fields.Boolean(
        help="Part of the quantity must be delivered immediately."
    )
    immediate_batch_weeks = fields.Integer(
        string="Immediate Batch (weeks)",
        help='What "immediate" means, in weeks from the start event.',
    )
    min_months = fields.Integer(string="Min. Months")
    max_months = fields.Integer(string="Max. Months")
    start_event = fields.Selection(
        selection=[("advance_payment_received", "Advance payment received")],
        default="advance_payment_received",
        required=True,
        help="The event all delivery periods of this group are counted from.",
    )
    boq_reference = fields.Char(string="BOQ Reference", help='e.g. "Annex-I"')

    # One2many is the REVERSE side of sale.order.line.delivery_group_id.
    # It adds no column here; it is a saved search "lines whose group is me".
    line_ids = fields.One2many(
        comodel_name="sale.order.line",
        inverse_name="delivery_group_id",
        string="PO Lines",
    )
    line_count = fields.Integer(string="Lines", compute="_compute_line_count")

    # Computed + stored: the value is calculated by the method, written to a
    # real column, and recalculated automatically whenever a field named in
    # @api.depends changes. Storing it lets us sort and filter on deadlines.
    immediate_batch_deadline = fields.Date(
        compute="_compute_deadlines", store=True
    )
    final_deadline = fields.Date(compute="_compute_deadlines", store=True)

    # A dotted path follows a relation: when the ORDER's advance payment date
    # changes, Odoo finds every group of that order and recomputes it.
    @api.depends(
        "order_id.advance_payment_received_date",
        "immediate_batch",
        "immediate_batch_weeks",
        "max_months",
    )
    def _compute_deadlines(self):
        for group in self:
            start = group.order_id.advance_payment_received_date
            # A compute method must assign EVERY computed field for EVERY
            # record, including the "no value" case.
            group.immediate_batch_deadline = (
                start + relativedelta(weeks=group.immediate_batch_weeks)
                if start and group.immediate_batch and group.immediate_batch_weeks
                else False
            )
            # relativedelta adds calendar months (31 Jan + 1 month = 28/29 Feb).
            group.final_deadline = (
                start + relativedelta(months=group.max_months)
                if start and group.max_months
                else False
            )

    @api.depends("line_ids")
    def _compute_line_count(self):
        for group in self:
            group.line_count = len(group.line_ids)
