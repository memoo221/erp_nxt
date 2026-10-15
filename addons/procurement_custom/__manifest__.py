# The manifest is how Odoo discovers a module: a folder is a module if (and only
# if) it contains this file. It is a plain Python dictionary.
{
    "name": "NXTVOLT Procurement",
    "summary": "Register client POs, request vendor quotes, compare, purchase, report",
    # Convention: <odoo version>.<module major>.<minor>.<patch>
    "version": "20.0.1.4.0",
    "category": "Sales",
    "author": "NXTVOLT",
    "license": "LGPL-3",
    # Modules that must be installed first. Their models/views are the ones we
    # extend. `sale` (pulled in by sale_management) already provides the
    # `incoterm` field, so sale_stock is not needed.
    # sale_margin: the line's Unit Cost is fed from our purchases (see
    # sale_order_line._compute_purchase_price).
    "depends": ["sale_management", "sale_margin", "contacts"],
    # Files loaded into the database on install/upgrade, IN THIS ORDER.
    # Order matters: a file may only reference things defined before it
    # (e.g. a menu needs its action, a view needs the model's access rules).
    "data": [
        "security/ir.access.csv",
        "data/decimal_precision.xml",
        "report/report_actions.xml",
        "report/report_status.xml",
        "report/report_client_po.xml",
        "report/report_quote_request.xml",
        "wizard/quote_request_wizard_views.xml",
        "views/procurement_quote_views.xml",
        "views/procurement_purchase_views.xml",
        "views/sale_order_views.xml",
        "views/menus.xml",
    ],
    # Sample records, loaded only into databases created with "Demo data".
    "demo": [
        "demo/demo_po_65282.xml",
    ],
    # True = shown as an "App" with its own icon on the Apps screen.
    "application": True,
    "installable": True,
}
