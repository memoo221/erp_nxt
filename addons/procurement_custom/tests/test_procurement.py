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

    def _purchase(self, line, qty, **extra):
        return self.env["procurement.purchase"].create(
            {"sale_line_id": line.id, "quantity": qty, **extra}
        )

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

    def test_only_one_chosen_quote_per_item(self):
        quote_a = self._quote(self.line_1, self.vendor_a, 80.0)
        quote_b = self._quote(self.line_1, self.vendor_b, 75.0)
        quote_a.state = "chosen"
        with self.assertRaises(ValidationError):
            quote_b.state = "chosen"

    def test_choose_action_rejects_the_others(self):
        quote_a = self._quote(self.line_1, self.vendor_a, 80.0)
        quote_b = self._quote(self.line_1, self.vendor_b, 75.0)
        quote_c = self._quote(self.line_1, self.vendor_c)  # no price yet
        other_item = self._quote(self.line_2, self.vendor_a, 10.0)

        with self.assertRaises(UserError):
            quote_c.action_choose()

        quote_b.action_choose()
        self.assertEqual(quote_b.state, "chosen")
        self.assertEqual(quote_a.state, "rejected")
        self.assertEqual(quote_c.state, "rejected")
        self.assertEqual(other_item.state, "quoted", "Quotes of other items are untouched")
        self.assertEqual(self.line_1.chosen_vendor_id, self.vendor_b)
        self.assertEqual(self.line_1.procurement_status, "vendor_chosen")
        self.assertEqual(self.order.items_chosen_count, 1)

        # Choosing another quote moves the choice instead of failing.
        quote_a.action_choose()
        self.assertEqual(quote_a.state, "chosen")
        self.assertEqual(quote_b.state, "rejected")
        self.assertEqual(self.line_1.chosen_vendor_id, self.vendor_a)

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
        self._quote(self.line_1, self.vendor_a, 80.0).action_choose()

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
        self._quote(self.line_1, self.vendor_a, 80.0).action_choose()
        self._purchase(self.line_1, 4)  # 4 x 80
        self.assertAlmostEqual(self.line_1.avg_purchase_price, 80.0)
        # (client price 100 - cost 80) x 4 obtained
        self.assertAlmostEqual(self.line_1.margin_amount, 80.0, places=2)

        self._purchase(self.line_1, 6, purchase_price=90.0)  # 6 x 90
        # weighted average = (4*80 + 6*90) / 10 = 86
        self.assertAlmostEqual(self.line_1.avg_purchase_price, 86.0)
        self.assertAlmostEqual(self.line_1.margin_amount, (100.0 - 86.0) * 10, places=2)

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
    # Reports
    # ------------------------------------------------------------------
    def _render(self, report_name, records):
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            f"procurement_custom.{report_name}", records.ids
        )
        return html.decode()

    def test_client_status_report_hides_vendors_and_costs(self):
        self._quote(self.line_1, self.vendor_a, 8123.45).action_choose()
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
