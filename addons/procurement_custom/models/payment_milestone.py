from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class ProcurementPaymentMilestone(models.Model):
    """One step of the client's payment schedule, e.g. "25% advance with LG".

    A NEW model: `_name` creates the table `procurement_payment_milestone`.
    """

    _name = "procurement.payment.milestone"
    _description = "Client PO Payment Milestone"
    # Default sort order of every search on this model.
    _order = "order_id, sequence, id"

    # Many2one = a foreign key column pointing at one sale.order row.
    # ondelete="cascade": deleting the order deletes its milestones too.
    # index=True: we constantly look milestones up by order.
    order_id = fields.Many2one(
        comodel_name="sale.order",
        string="Client PO",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Milestone", required=True)
    percent = fields.Float(string="Percent (%)", digits=(5, 2))
    requires_lg = fields.Boolean(
        string="Requires LG", help="A Letter of Guarantee must be issued for this payment."
    )
    lg_type = fields.Char(string="LG Type", help='e.g. "unconditional and irrevocable"')
    trigger = fields.Selection(
        selection=[
            ("on_advance", "On advance"),
            ("on_delivery_completion", "On delivery completion"),
            ("on_partial_delivery", "On partial delivery"),
        ],
        required=True,
        default="on_advance",
    )
    collection_days = fields.Integer(
        help="Days the client has to pay once this milestone is due."
    )
    notes = fields.Char()

    # SQL constraint: enforced by PostgreSQL itself (a CHECK on the table), so
    # it holds no matter how the row is written. Good for simple per-row rules.
    _percent_range = models.Constraint(
        "CHECK(percent >= 0 AND percent <= 100)",
        "A milestone percent must be between 0 and 100.",
    )

    # Python constraint: runs after create/write when one of the listed fields
    # was part of the change. Needed here because the rule spans SEVERAL rows
    # (all milestones of the order), which a per-row CHECK cannot express.
    @api.constrains("percent", "order_id")
    def _check_total_percent(self):
        # `self` is a recordset: possibly many milestones, of several orders.
        for order in self.order_id:
            total = sum(order.payment_milestone_ids.mapped("percent"))
            # Never compare floats with a bare `>`: 33.33 + 33.33 + 33.34 may
            # come out as 100.00000000000001.
            if float_compare(total, 100.0, precision_digits=2) > 0:
                raise ValidationError(
                    self.env._(
                        "The payment milestones of %(order)s add up to %(total).2f%%. "
                        "They cannot exceed 100%%.",
                        order=order.display_name,
                        total=total,
                    )
                )
