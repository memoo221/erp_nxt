from odoo import api, models


class AccountMove(models.Model):
    # Invoices are account.move records; their rows are account.move.line.
    _inherit = "account.move"

    # Odoo keeps the line prices as they are when the currency of a draft
    # invoice is changed: $29.56 silently becomes 29.56 EGP. Here the prices
    # are converted instead, at the rate shown next to the currency
    # ("1 USD = 50.46 EGP"). That rate is proposed from Accounting > Currencies
    # but may be typed over; the prices follow it either way.
    #
    # An @api.onchange method runs in the browser's form while the user edits,
    # before anything is saved. `_origin` is the record as last saved.
    @api.onchange("currency_id", "invoice_currency_rate")
    def _onchange_currency_convert_prices(self):
        if self.state != "draft" or not self.currency_id:
            return
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == "product"):
            sale_line = line.sale_line_ids[:1]
            if sale_line:
                # Invoiced from a client PO: start again from the PO's own
                # price, so switching currency back and forth never drifts.
                price, currency = sale_line.price_unit, sale_line.currency_id
            elif line._origin:
                # Likewise from the saved price, in the saved currency.
                price, currency = line._origin.price_unit, self._origin.currency_id
            else:
                # A row added since the last save: its price is in the
                # currency that was shown when it was typed.
                continue
            line.price_unit = self._to_invoice_currency(price, currency)

    def _to_invoice_currency(self, amount, currency):
        """Convert `amount` from `currency` with this invoice's own rate.

        invoice_currency_rate says how many units of the invoice currency make
        one unit of the company currency, so the amount goes through the
        company currency first.
        """
        if not currency or currency == self.currency_id:
            return amount
        company_currency = self.company_currency_id
        if currency != company_currency:
            amount = currency._convert(
                amount,
                company_currency,
                company=self.company_id,
                date=self._get_invoice_currency_rate_date(),
                round=False,
            )
        return amount * (self.invoice_currency_rate or 1.0)
