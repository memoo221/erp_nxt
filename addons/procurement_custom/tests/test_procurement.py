import datetime

from psycopg2 import IntegrityError

from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tests import Form, TransactionCase, tagged
from odoo.tools import mute_logger


# TransactionCase: every test method runs inside a database transaction that is
# rolled back afterwards, so tests never leave data behind and cannot disturb
# each other.
#
# post_install: run after ALL modules are installed (the realistic situation),
# instead of right after this module's own installation.
@tagged("post_install", "-at_install")
class TestProcurement(TransactionCase):

    # setUpClass runs once for the whole class; what it creates is visible to
    # every test and rolled back at the very end.
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.usd = cls.env.ref("base.USD")
        cls.tax_14 = cls.env["account.tax"].create(
            {"name": "VAT 14% (test)", "amount": 14, "amount_type": "percent", "type_tax_use": "sale"}
        )
        cls.pricelist_usd = cls.env["product.pricelist"].create(
            {"name": "USD (test)", "currency_id": cls.usd.id}
        )
        cls.client = cls.env["res.partner"].create({"name": "Test Client", "is_company": True})
        cls.vendor_a, cls.vendor_b, cls.vendor_c = cls.env["res.partner"].create(
            [
                {"name": name, "is_company": True, "supplier_rank": 1}
                for name in ("Alpha Vendor", "Bravo Vendor", "Charlie Vendor")
            ]
        )
        products = cls.env["product.product"].create(
            [{"name": f"Test Item {i}", "default_code": f"TST{i}"} for i in (1, 2, 3)]
        )
        # Line 1: 10 x 100.00 = 1000.00   Line 2: 4 x 12.50 = 50.00
        # Line 3:  3 x 1.123456 = 3.37 (printed total deliberately wrong)
        cls.order = cls.env["sale.order"].create(
            {
                "is_client_po": True,
                "partner_id": cls.client.id,
                "pricelist_id": cls.pricelist_usd.id,
                "client_order_ref": "TEST-PO-1",
                "client_pr_number": "TEST-PR-1",
                "order_line": [
                    cls._line_vals(products[0], 1, 10, 100.0, 1000.0),
                    cls._line_vals(products[1], 2, 4, 12.5, 50.0),
                    cls._line_vals(products[2], 3, 3, 1.123456, 99.0),
                ],
            }
        )
        cls.line_1, cls.line_2, cls.line_3 = cls.order.order_line

    @classmethod
    def _line_vals(cls, product, line_no, qty, price, total_printed):
        return Command.create(
            {
                "product_id": product.id,
                "client_line_no": line_no,
                "client_item_code": product.default_code,
                "product_uom_qty": qty,
                "price_unit": price,
                "tax_ids": [Command.set(cls.tax_14.ids)],
                "total_printed": total_printed,
            }
        )

    def _quote(self, line, vendor, price=0.0):
        return self.env["procurement.quote"].create(
            {"sale_line_id": line.id, "vendor_id": vendor.id, "quoted_price": price}
        )

    def _choose(self, quote, qty=None):
        """Type the quantity on the quote row (if given), then press Choose."""
        if qty is not None:
            quote.qty_to_buy = qty
        return quote.action_choose()

    def _purchase(self, line, qty, **extra):
        """A confirmed purchase recorded by hand."""
        purchase = self.env["procurement.purchase"].create(
            {"sale_line_id": line.id, "quantity": qty, **extra}
        )
        purchase.action_confirm()
        return purchase

    # ------------------------------------------------------------------
    # Totals as printed
    # ------------------------------------------------------------------
    def test_demo_po_totals_match(self):
        """The real PO 65282: Odoo's totals equal the printed ones."""
        po = self.env.ref("procurement_custom.client_po_65282", raise_if_not_found=False)
        if not po:
            self.skipTest("Demo data is not installed in this database.")
        self.assertEqual(len(po.order_line), 14)
        self.assertAlmostEqual(po.amount_untaxed, 348827.48, places=2)
        self.assertAlmostEqual(po.amount_total, 397663.33, places=2)
        self.assertTrue(po.totals_match)
        self.assertTrue(all(po.order_line.mapped("line_total_match")))

        # Change one printed total: the flag must drop.
        po.grand_total_printed = 397663.35
        self.assertFalse(po.totals_match)
        po.grand_total_printed = 397663.33
        self.assertTrue(po.totals_match)

        # The VAT is printed unrounded (48835.8472); 0.0028 away is tolerated,
        # a whole unit is not.
        po.tax_vat_printed = 48836.8472
        self.assertFalse(po.totals_match)

    def test_totals_match_tolerance(self):
        order = self.order
        self.assertFalse(order.totals_match, "No printed totals entered yet")
        order.write(
            {
                "total_po_amount_printed": order.amount_untaxed,
                "tax_table_printed": 0.0,
                "tax_vat_printed": order.amount_tax,
                "grand_total_printed": order.amount_total,
            }
        )
        self.assertTrue(order.totals_match)
        order.grand_total_printed = order.amount_total + 0.01
        self.assertTrue(order.totals_match, "A difference of 0.01 is within tolerance")
        order.grand_total_printed = order.amount_total + 0.02
        self.assertFalse(order.totals_match)

    def test_line_total_match(self):
        self.assertTrue(self.line_1.line_total_match)
        self.assertTrue(self.line_2.line_total_match)
        self.assertFalse(self.line_3.line_total_match)
        self.assertEqual(self.order.lines_total_mismatch_count, 1)
        # Correct the printed total: the stored flag is recomputed.
        self.line_3.total_printed = self.line_3.price_subtotal
        self.assertTrue(self.line_3.line_total_match)
        self.assertEqual(self.order.lines_total_mismatch_count, 0)

    def test_client_line_no_unique_per_order(self):
        # The unique index lives in PostgreSQL, so the error only appears when
        # the pending write is actually sent to the database (flush).
        # mute_logger hides the expected SQL error from the test log.
        # The savepoint lets the test transaction continue after the failed SQL.
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self.line_2.client_line_no = 1
            self.env.flush_all()

    def test_client_line_no_may_repeat_across_orders(self):
        other = self.order.copy()
        self.assertEqual(sorted(other.order_line.mapped("client_line_no")), [1, 2, 3])

    # ------------------------------------------------------------------
    # Payment milestones
    # ------------------------------------------------------------------
    def test_milestone_percent_cannot_exceed_100(self):
        Milestone = self.env["procurement.payment.milestone"]
        Milestone.create(
            [
                {"order_id": self.order.id, "name": "Advance", "percent": 25},
                {"order_id": self.order.id, "name": "Advance LG", "percent": 25},
                {"order_id": self.order.id, "name": "Delivery", "percent": 50},
            ]
        )
        with self.assertRaises(ValidationError):
            Milestone.create({"order_id": self.order.id, "name": "Too much", "percent": 0.5})
        with self.assertRaises(ValidationError):
            self.order.payment_milestone_ids[0].percent = 26

    # ------------------------------------------------------------------
    # Delivery groups
    # ------------------------------------------------------------------
    def test_delivery_group_deadlines(self):
        group = self.env["procurement.delivery.group"].create(
            {
                "order_id": self.order.id,
                "name": "Filters",
                "immediate_batch": True,
                "immediate_batch_weeks": 2,
                "min_months": 1,
                "max_months": 4,
            }
        )
        self.assertFalse(group.immediate_batch_deadline)
        self.assertFalse(group.final_deadline)

        # Setting the date on the ORDER recomputes the stored fields of the group.
        self.order.advance_payment_received_date = datetime.date(2026, 8, 2)
        self.assertEqual(group.immediate_batch_deadline, datetime.date(2026, 8, 16))
        self.assertEqual(group.final_deadline, datetime.date(2026, 12, 2))

        group.immediate_batch = False
        self.assertFalse(group.immediate_batch_deadline)

        # Calendar months: 31 October + 4 months = 28 February (2027).
        self.order.advance_payment_received_date = datetime.date(2026, 10, 31)
        self.assertEqual(group.final_deadline, datetime.date(2027, 2, 28))

    def test_delivery_group_must_belong_to_same_order(self):
        other_order = self.order.copy()
        foreign_group = self.env["procurement.delivery.group"].create(
            {"order_id": other_order.id, "name": "Other"}
        )
        with self.assertRaises(ValidationError):
            self.line_1.delivery_group_id = foreign_group

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------
    def test_quote_states_and_lowest(self):
        quote_a = self._quote(self.line_1, self.vendor_a)
        self.assertEqual(self.line_2.procurement_status, "not_quoted", "Nobody asked yet")
        self.assertEqual(quote_a.state, "requested")
        # Asked, but no price back yet: visible on the PO line as such.
        self.assertEqual(self.line_1.procurement_status, "quote_requested")

        quote_a.quoted_price = 80.0
        self.assertEqual(quote_a.state, "quoted", "Entering a price marks the quote as quoted")
        self.assertTrue(quote_a.quote_date)
        self.assertEqual(self.line_1.procurement_status, "quoted")

        quote_b = self._quote(self.line_1, self.vendor_b, 75.0)
        self.assertEqual(quote_b.state, "quoted")
        self.assertTrue(quote_b.is_lowest)
        self.assertFalse(quote_a.is_lowest)

    def test_chosen_quantities_cannot_exceed_ordered(self):
        quote_a = self._quote(self.line_1, self.vendor_a, 80.0)
        quote_b = self._quote(self.line_1, self.vendor_b, 75.0)
        quote_a.write({"qty_to_buy": 6, "state": "chosen"})
        with self.assertRaises(ValidationError):
            quote_b.write({"qty_to_buy": 5, "state": "chosen"})  # 6 + 5 > 10
        quote_b.write({"qty_to_buy": 4, "state": "chosen"})  # 6 + 4 = 10

    def test_choose_creates_draft_purchase_and_rejects_the_others(self):
        quote_a = self._quote(self.line_1, self.vendor_a, 80.0)
        quote_b = self._quote(self.line_1, self.vendor_b, 75.0)
        quote_c = self._quote(self.line_1, self.vendor_c)  # no price yet
        other_item = self._quote(self.line_2, self.vendor_a, 10.0)

        with self.assertRaises(UserError):
            quote_c.action_choose()

        # No quantity typed: the vendor gets the whole ordered quantity.
        self._choose(quote_b)
        self.assertEqual(quote_b.state, "chosen")
        self.assertEqual(quote_b.qty_to_buy, 10)
        self.assertEqual(quote_b.amount_to_buy, 750.0)
        self.assertEqual(quote_a.state, "rejected")
        self.assertEqual(quote_c.state, "rejected")
        self.assertEqual(other_item.state, "quoted", "Quotes of other items are untouched")
        self.assertEqual(self.line_1.chosen_vendor_id, self.vendor_b)
        self.assertEqual(self.order.items_chosen_count, 1)

        purchase = quote_b.purchase_ids
        self.assertEqual(len(purchase), 1)
        self.assertEqual(purchase.state, "draft")
        self.assertEqual(purchase.vendor_id, self.vendor_b)
        self.assertEqual(purchase.quantity, 10)
        self.assertEqual(purchase.purchase_price, 75.0)
        self.assertEqual(purchase.amount, 750.0)
        # A draft purchase is only planned: nothing obtained yet.
        self.assertEqual(self.line_1.qty_obtained, 0)
        self.assertEqual(self.line_1.procurement_status, "vendor_chosen")

        purchase.action_confirm()
        self.assertEqual(self.line_1.qty_obtained, 10)
        self.assertEqual(self.line_1.procurement_status, "obtained")
        self.assertEqual(self.order.items_obtained_count, 1)

        # Everything is assigned: another vendor cannot be chosen on top.
        with self.assertRaises(UserError):
            quote_a.action_choose()
        # A confirmed purchase blocks undoing the choice...
        with self.assertRaises(UserError):
            quote_b.action_reset()
        # ...until it is set back to draft.
        purchase.action_draft()
        self.assertEqual(self.line_1.qty_obtained, 0)
        quote_b.action_reset()
        self.assertFalse(purchase.exists(), "Undo deletes the draft purchase")
        self.assertEqual(quote_b.state, "quoted")
        self.assertEqual(quote_a.state, "quoted", "Rejected quotes are open again")
        self.assertEqual(quote_c.state, "requested")
        self.assertEqual(self.line_1.procurement_status, "quoted")

    def test_add_quote_returns_to_comparison_without_choosing(self):
        quote = self._quote(self.line_1, self.vendor_a, 80.0)
        action = quote.action_add()
        self.assertEqual(action["res_model"], "procurement.quote")
        self.assertEqual(action["domain"], [("order_id", "=", self.order.id)])
        self.assertEqual(quote.state, "quoted", "Adding never chooses")
        self.assertFalse(self.line_1.purchase_ids)

    def test_change_quantity_of_chosen_vendor(self):
        quote_a = self._quote(self.line_1, self.vendor_a, 80.0)
        quote_b = self._quote(self.line_1, self.vendor_b, 75.0)
        self._choose(quote_b)  # all 10
        self.assertEqual(quote_a.state, "rejected")
        purchase = quote_b.purchase_ids

        # Lowering the chosen quantity: the draft purchase follows and the
        # other vendor is open again for the rest.
        quote_b.qty_to_buy = 6
        self.assertEqual(purchase.quantity, 6)
        self.assertEqual(purchase.amount, 450.0)
        self.assertEqual(quote_a.state, "quoted")
        self._choose(quote_a)
        self.assertEqual(quote_a.qty_to_buy, 4)

        # Raising it past the ordered quantity is refused.
        with self.assertRaises(ValidationError):
            quote_b.qty_to_buy = 7
        # Clearing it is refused: Undo is the way to drop a vendor.
        with self.assertRaises(UserError):
            quote_b.qty_to_buy = 0
        # Once the purchase is confirmed, the quantity is locked.
        purchase.action_confirm()
        with self.assertRaises(UserError):
            quote_b.qty_to_buy = 5

    def test_split_item_between_vendors(self):
        quote_a = self._quote(self.line_1, self.vendor_a, 80.0)
        quote_b = self._quote(self.line_1, self.vendor_b, 75.0)
        quote_c = self._quote(self.line_1, self.vendor_c, 90.0)

        self.assertEqual(quote_a.qty_needed, 10)
        self._choose(quote_b, 6)
        # Every quote of the item now shows the 4 still needed.
        self.assertEqual(quote_a.qty_needed, 4)
        self.assertEqual(quote_c.qty_needed, 4)
        # 4 still unassigned: the other quotes stay open.
        self.assertEqual(quote_a.state, "quoted")
        self.assertEqual(quote_c.state, "quoted")

        with self.assertRaises(ValidationError):
            self._choose(quote_a, 5)  # only 4 left
        # Back to empty: Choose takes the 4 that are left.
        quote_a.qty_to_buy = 0
        self._choose(quote_a)
        self.assertEqual(quote_a.qty_to_buy, 4)
        self.assertEqual(quote_c.state, "rejected", "Fully assigned: the rest is rejected")

        purchases = self.line_1.purchase_ids
        self.assertEqual(sorted(purchases.mapped("quantity")), [4, 6])
        self.assertEqual(set(purchases.mapped("state")), {"draft"})

        quote_b.purchase_ids.action_confirm()
        self.assertEqual(self.line_1.qty_obtained, 6)
        self.assertEqual(self.line_1.procurement_status, "partially_obtained")
        quote_a.purchase_ids.action_confirm()
        self.assertEqual(self.line_1.procurement_status, "obtained")
        # (6 x 75 + 4 x 80) / 10
        self.assertAlmostEqual(self.line_1.avg_purchase_price, 77.0)

    def test_request_wizard_creates_quotes_without_duplicates(self):
        Wizard = self.env["procurement.quote.request.wizard"].with_context(
            default_order_id=self.order.id
        )
        Wizard.create(
            {
                "line_ids": [
                    Command.create({"vendor_id": self.vendor_a.id, "all_items": True}),
                    Command.create(
                        {
                            "vendor_id": self.vendor_b.id,
                            "sale_line_ids": [Command.set([self.line_1.id])],
                        }
                    ),
                ]
            }
        ).action_create_requests()
        quotes = self.order.order_line.quote_ids
        self.assertEqual(len(quotes), 4)
        self.assertEqual(set(quotes.mapped("state")), {"requested"})

        # Running it again for the same vendor adds nothing.
        Wizard.create(
            {"line_ids": [Command.create({"vendor_id": self.vendor_a.id, "all_items": True})]}
        ).action_create_requests()
        self.assertEqual(len(self.order.order_line.quote_ids), 4)

    def test_request_wizard_form_behaviour(self):
        """Drive the dialog the way the browser does (Form simulates the UI)."""
        action = self.order.action_request_quotes()
        form = Form(self.env[action["res_model"]].with_context(**action["context"]))
        with form.line_ids.new() as row:
            row.vendor_id = self.vendor_a
            # A new row starts with no item selected...
            self.assertEqual(len(row.sale_line_ids), 0)
            row.sale_line_ids.add(self.line_2)
        with form.line_ids.new() as row:
            row.vendor_id = self.vendor_b
            row.all_items = True
            self.assertEqual(len(row.sale_line_ids), 3)
        wizard = form.save()
        # Every row knows its PO; that is what restricts the Items dropdown.
        self.assertEqual(wizard.line_ids.order_id, self.order)
        wizard.action_create_requests()
        quotes = self.order.order_line.quote_ids
        self.assertEqual(
            quotes.filtered(lambda q: q.vendor_id == self.vendor_a).sale_line_id, self.line_2
        )
        self.assertEqual(len(quotes.filtered(lambda q: q.vendor_id == self.vendor_b)), 3)

    def test_request_wizard_requires_items(self):
        wizard = self.env["procurement.quote.request.wizard"].create(
            {
                "order_id": self.order.id,
                "line_ids": [Command.create({"vendor_id": self.vendor_a.id})],
            }
        )
        with self.assertRaises(UserError):
            wizard.action_create_requests()

    def test_item_dropdown_label_and_search(self):
        Line = self.env["sale.order.line"]
        short = self.line_1.with_context(procurement_item_name=True).display_name
        self.assertEqual(short, "1. TST1 [TST1] Test Item 1")
        # Without the context key, Odoo's standard label is unchanged.
        self.assertTrue(self.line_1.display_name.startswith(self.order.name))
        # Typing the client's item code in a dropdown finds the line.
        found = Line.name_search("TST2", [("order_id", "=", self.order.id)])
        self.assertEqual([res_id for res_id, _name in found], self.line_2.ids)

    # ------------------------------------------------------------------
    # Purchases
    # ------------------------------------------------------------------
    def test_partial_purchases_quantities_and_status(self):
        self._choose(self._quote(self.line_1, self.vendor_a, 80.0))

        first = self._purchase(self.line_1, 4)
        # Defaults come from the chosen quote.
        self.assertEqual(first.vendor_id, self.vendor_a)
        self.assertEqual(first.purchase_price, 80.0)
        self.assertEqual(first.currency_id, self.usd)

        self.assertEqual(self.line_1.qty_obtained, 4)
        self.assertEqual(self.line_1.qty_remaining, 6)
        self.assertEqual(self.line_1.procurement_status, "partially_obtained")
        self.assertEqual(self.order.items_obtained_count, 0)

        # Second purchase at a different price completes the item.
        self._purchase(self.line_1, 6, purchase_price=90.0)
        self.assertEqual(self.line_1.qty_obtained, 10)
        self.assertEqual(self.line_1.qty_remaining, 0)
        self.assertEqual(self.line_1.procurement_status, "obtained")
        self.assertEqual(self.order.item_count, 3)
        self.assertEqual(self.order.items_obtained_count, 1)
        self.assertAlmostEqual(self.order.progress_percent, 100 / 3, places=2)

        # Deleting a purchase rolls the figures back.
        first.unlink()
        self.assertEqual(self.line_1.qty_obtained, 6)
        self.assertEqual(self.line_1.procurement_status, "partially_obtained")

    def test_margin(self):
        self._choose(self._quote(self.line_1, self.vendor_a, 80.0))
        self._purchase(self.line_1, 4)  # 4 x 80
        self.assertAlmostEqual(self.line_1.avg_purchase_price, 80.0)
        # (client price 100 - cost 80) x 4 obtained
        self.assertAlmostEqual(self.line_1.margin_amount, 80.0, places=2)

        # Odoo's own margin (Sale Margin app) uses the same cost.
        self.assertAlmostEqual(self.line_1.purchase_price, 80.0)
        self.assertAlmostEqual(self.line_1.margin, 1000.0 - 80.0 * 10, places=2)

        self._purchase(self.line_1, 6, purchase_price=90.0)  # 6 x 90
        # weighted average = (4*80 + 6*90) / 10 = 86
        self.assertAlmostEqual(self.line_1.avg_purchase_price, 86.0)
        self.assertAlmostEqual(self.line_1.margin_amount, (100.0 - 86.0) * 10, places=2)
        self.assertAlmostEqual(self.line_1.purchase_price, 86.0)
        self.assertAlmostEqual(self.line_1.margin, (100.0 - 86.0) * 10, places=2)

    def test_margin_with_purchase_in_another_currency(self):
        egp = self.env.ref("base.EGP")
        # Fix the exchange rate for the test: 1 USD = 50 EGP. A rate says how
        # many units of the currency equal one unit of the company currency;
        # only the RATIO between two rates matters for a conversion, so this
        # works whatever the company currency is.
        Rate = self.env["res.currency.rate"]
        Rate.search([("currency_id", "in", (self.usd | egp).ids)]).unlink()
        Rate.create(
            [
                {"currency_id": self.usd.id, "rate": 1.0, "name": "2020-01-01"},
                {"currency_id": egp.id, "rate": 50.0, "name": "2020-01-01"},
            ]
        )
        self._purchase(
            self.line_2, 4, vendor_id=self.vendor_a.id, purchase_price=500.0, currency_id=egp.id
        )
        # 500 EGP = 10 USD; client price 12.50
        self.assertAlmostEqual(self.line_2.avg_purchase_price, 10.0)
        self.assertAlmostEqual(self.line_2.margin_amount, (12.5 - 10.0) * 4, places=2)
        self.assertEqual(self.line_2.procurement_status, "obtained")

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------
    def test_invoice_only_what_was_obtained(self):
        self.order.action_confirm()
        # Nothing bought yet: nothing to invoice, whatever the invoicing policy.
        self.assertEqual(self.line_1.qty_to_invoice, 0)
        with self.assertRaises(UserError):
            self.order._create_invoices()

        self._purchase(self.line_1, 4, vendor_id=self.vendor_a.id, purchase_price=80.0)
        self.assertEqual(self.line_1.qty_to_invoice, 4)
        first = self.order._create_invoices()
        self.assertEqual(first.invoice_line_ids.quantity, 4, "Only the 4 obtained, not the 10 ordered")
        first.action_post()
        self.assertEqual(self.line_1.qty_to_invoice, 0)

        # Six more obtained: the next invoice takes just those.
        self._purchase(self.line_1, 6, vendor_id=self.vendor_a.id, purchase_price=80.0)
        self.assertEqual(self.line_1.qty_to_invoice, 6)
        self.assertEqual(self.order._create_invoices().invoice_line_ids.quantity, 6)

    def test_invoice_currency_change_converts_prices(self):
        egp = self.env.ref("base.EGP")
        egp.active = True
        Rate = self.env["res.currency.rate"]
        Rate.search([("currency_id", "in", (self.usd | egp).ids)]).unlink()
        Rate.create(
            [
                {"currency_id": self.usd.id, "rate": 1.0, "name": "2020-01-01"},
                {"currency_id": egp.id, "rate": 50.0, "name": "2020-01-01"},
            ]
        )
        self.order.action_confirm()
        self._purchase(self.line_1, 10, vendor_id=self.vendor_a.id, purchase_price=80.0)
        invoice = self.order._create_invoices()
        line = invoice.invoice_line_ids.filtered(lambda l: l.sale_line_ids == self.line_1)
        self.assertEqual(line.price_unit, 100.0)

        if invoice.company_currency_id != self.usd:
            self.skipTest("The rate checks below assume a company in USD.")

        with Form(invoice) as form:
            form.currency_id = egp
            # The rate box proposes the stored rate.
            self.assertAlmostEqual(form.invoice_currency_rate, 50.0)
        self.assertAlmostEqual(line.price_unit, 5000.0)

        # Typing another rate in the box converts again, from the PO price.
        # (The box is a widget on a hidden field, which Form cannot type in;
        # the browser sets the field and the same onchange runs.)
        invoice.invoice_currency_rate = 60.0
        invoice._onchange_currency_convert_prices()
        self.assertAlmostEqual(line.price_unit, 6000.0)

        # Back to the PO's currency: exactly the PO price again.
        with Form(invoice) as form:
            form.currency_id = self.usd
        self.assertAlmostEqual(line.price_unit, 100.0)

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def _render(self, report_name, records):
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            f"procurement_custom.{report_name}", records.ids
        )
        return html.decode()

    def test_client_status_report_hides_vendors_and_costs(self):
        self._choose(self._quote(self.line_1, self.vendor_a, 8123.45))
        self._quote(self.line_1, self.vendor_b, 8234.56)
        self._purchase(self.line_1, 4, purchase_price=8345.67)

        html = self._render("report_client_status", self.order)
        # What the client must see
        for expected in ("TEST-PO-1", "TEST-PR-1", "TST1", "Partially obtained"):
            self.assertIn(expected, html)
        # What the client must never see
        for secret in (
            "Alpha Vendor", "Bravo Vendor", "Charlie Vendor",
            "8123", "8,123", "8234", "8,234", "8345", "8,345",
            "Margin", "Vendor", "CONFIDENTIAL",
        ):
            self.assertNotIn(secret, html)

        # The internal report is the one that shows them.
        internal = self._render("report_internal_status", self.order)
        for expected in ("Alpha Vendor", "8345.670000", "Margin", "CONFIDENTIAL"):
            self.assertIn(expected, internal)

    def test_quote_request_report_hides_client_data(self):
        quotes = self._quote(self.line_1, self.vendor_a) | self._quote(self.line_3, self.vendor_b)
        html = self._render("report_quote_request", quotes)
        self.assertEqual(html.count("Request for Quotation"), 2, "One page per vendor")
        for expected in ("Alpha Vendor", "Bravo Vendor", "TST1", "TST3"):
            self.assertIn(expected, html)
        # Neither who the client is, nor the client's PO/PR numbers or prices.
        for secret in ("Test Client", "TEST-PO-1", "TEST-PR-1", "1.123456"):
            self.assertNotIn(secret, html)
