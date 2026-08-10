# Copyright (c) 2025, Gurudatt Kulkarni and contributors
# For license information, please see license.txt

from functools import partial

import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
from frappe.test_runner import make_test_objects
from frappe.utils import getdate
from frappe.utils.nestedset import get_root_of


def before_tests():
	"""Set up test environment for Gate Entry module."""
	frappe.clear_cache()

	# Ensure _Test Company exists (required by ERPNext and other test records)
	# This must be created early as compat_preload_test_records_upfront may run before before_tests()
	ensure_test_company_exists()

	# Set up company if it doesn't exist
	company_name = "Wind Power LLP"
	if not frappe.db.exists("Company", company_name):
		today = getdate()
		year = today.year if today.month > 3 else today.year - 1

		setup_complete(
			{
				"currency": "INR",
				"full_name": "Test User",
				"company_name": company_name,
				"timezone": "Asia/Kolkata",
				"company_abbr": "WP",
				"industry": "Manufacturing",
				"country": "India",
				"fy_start_date": f"{year}-04-01",
				"fy_end_date": f"{year + 1}-03-31",
				"language": "English",
				"company_tagline": "Testing",
				"email": "test-wp@example.com",
				"password": "test",
				"chart_of_accounts": "Standard",
				"company_gstin": "24AAQCA8719H1ZA",
				"default_gst_rate": "18.0",
				"enable_audit_trail": 0,
			}
		)
	set_default_settings_for_tests()
	create_test_records()
	ensure_warehouses_exist()
	set_default_company_for_tests()
	frappe.db.commit()
	frappe.clear_cache()
	frappe.flags.country = "India"
	frappe.flags.skip_test_records = True
	frappe.enqueue = partial(frappe.enqueue, now=True)


def ensure_test_company_exists():
	"""Ensure _Test Company exists for compatibility with ERPNext test records."""
	if not frappe.db.exists("Company", "_Test Company"):
		try:
			from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
			from frappe.utils.data import now_datetime

			current_year = now_datetime().year
			setup_complete(
				{
					"currency": "INR",
					"full_name": "Test User",
					"company_name": "_Test Company",
					"timezone": "Asia/Kolkata",
					"company_abbr": "_TC",
					"industry": "Manufacturing",
					"country": "India",
					"fy_start_date": f"{current_year}-01-01",
					"fy_end_date": f"{current_year}-12-31",
					"language": "English",
					"company_tagline": "Testing",
					"email": "test@example.com",
					"password": "test",
					"chart_of_accounts": "Standard",
				}
			)
			frappe.db.commit()
		except Exception as exc:
			# Log but don't fail - this is a compatibility measure
			frappe.log_error(
				message=f"Failed to create _Test Company: {exc}",
				title="Gate Entry Test Setup - _Test Company",
			)


def set_default_settings_for_tests():
	"""Set default settings required for Gate Entry tests."""
	# Set default groups (like ERPNext and india_compliance)
	for key in ("Customer Group", "Supplier Group", "Item Group", "Territory"):
		frappe.db.set_default(frappe.scrub(key), get_root_of(key))

	# Stock Settings
	frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)

	# Ensure default UOM is set (in case ensure_uoms didn't set it)
	if frappe.db.exists("UOM", "Nos"):
		frappe.db.set_single_value("Stock Settings", "stock_uom", "Nos")

	# Enable Sandbox Mode in GST Settings (if india_compliance app is installed)
	# Check if GST Settings doctype exists (India Compliance app might not be installed)
	if frappe.db.exists("DocType", "GST Settings"):
		try:
			frappe.db.set_single_value("GST Settings", "sandbox_mode", 1)
		except Exception:
			# Handle cases where GST Settings document doesn't exist or other errors
			pass


def create_test_records():
	"""Create test records from test_records.json if it exists."""
	try:
		import os

		test_records_path = frappe.get_app_path("gate_entry", "tests", "test_records.json")
		if os.path.exists(test_records_path):
			test_records = frappe.get_file_json(test_records_path)

			for doctype, data in test_records.items():
				make_test_objects(doctype, data)
				if doctype == "Company":
					add_companies_to_fiscal_year(data)
	except Exception as exc:
		frappe.log_error(
			message=f"Failed to create test records: {exc}",
			title="Gate Entry Test Setup - Test Records",
		)


def ensure_warehouses_exist():
	"""Ensure default warehouses exist for the test company."""
	company_name = "Wind Power LLP"
	company_abbr = "WP"

	if not frappe.db.exists("Company", company_name):
		return

	try:
		from frappe import _

		# Reload company to trigger on_update which creates warehouses
		company = frappe.get_doc("Company", company_name)

		# Check if any warehouse exists for this company
		existing_warehouses = frappe.db.get_all(
			"Warehouse", filters={"company": company_name}, fields=["name"], limit=1
		)

		# If no warehouses exist, trigger company.on_update() to create default warehouses
		if not existing_warehouses:
			company.flags.ignore_validate = True
			company.save(ignore_permissions=True)

		# Verify warehouses exist, create if missing
		required_warehouses = [
			{"name": "Stores", "is_group": 0},
			{"name": "Finished Goods", "is_group": 0},
			{"name": "Work In Progress", "is_group": 0},
			{"name": "Goods In Transit", "is_group": 0, "warehouse_type": "Transit"},
			{"name": "Subcontractor", "is_group": 0},
		]

		# Get parent warehouse (All Warehouses)
		parent_warehouse = frappe.db.get_value(
			"Warehouse", {"warehouse_name": "All Warehouses", "company": company_name}, "name"
		)

		if not parent_warehouse:
			# Create parent warehouse first
			parent_wh = frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": "All Warehouses",
					"is_group": 1,
					"company": company_name,
				}
			)
			parent_wh.flags.ignore_permissions = True
			parent_wh.flags.ignore_mandatory = True
			parent_wh.insert(ignore_permissions=True)
			parent_warehouse = parent_wh.name
		for wh_info in required_warehouses:
			warehouse_full_name = f"{wh_info['name']} - {company_abbr}"
			# Check by full name (with abbreviation) first
			if not frappe.db.exists("Warehouse", warehouse_full_name):
				# Also check by warehouse_name and company
				if not frappe.db.exists(
					"Warehouse", {"warehouse_name": wh_info["name"], "company": company_name}
				):
					warehouse = frappe.get_doc(
						{
							"doctype": "Warehouse",
							"warehouse_name": wh_info["name"],
							"is_group": wh_info.get("is_group", 0),
							"company": company_name,
							"parent_warehouse": parent_warehouse,
							"warehouse_type": wh_info.get("warehouse_type"),
						}
					)
					warehouse.flags.ignore_permissions = True
					warehouse.flags.ignore_mandatory = True
					warehouse.insert(ignore_permissions=True)

	except Exception as exc:
		frappe.log_error(
			message=f"Failed to ensure warehouses exist: {exc}",
			title="Gate Entry Test Setup - Warehouses",
		)
		# Re-raise to make test failures visible
		raise


def set_default_company_for_tests():
	"""Set default company and configure it for tests."""
	company_name = "Wind Power LLP"
	if frappe.db.exists("Company", company_name):
		# stock settings
		frappe.db.set_value(
			"Company",
			company_name,
			{
				"enable_perpetual_inventory": 1,
				"default_inventory_account": "Stock In Hand - WP",
				"stock_adjustment_account": "Stock Adjustment - WP",
				"stock_received_but_not_billed": "Stock Received But Not Billed - WP",
			},
		)

		# set default company
		global_defaults = frappe.get_single("Global Defaults")
		global_defaults.default_company = company_name
		global_defaults.save(ignore_permissions=True)


def add_companies_to_fiscal_year(data):
	fy = get_fiscal_year(getdate(), as_dict=True)
	doc = frappe.get_doc("Fiscal Year", fy.name)
	fy_companies = [row.company for row in doc.companies]

	for company in data:
		if (company_name := company["company_name"]) not in fy_companies:
			doc.append("companies", {"company": company_name})

	doc.save(ignore_permissions=True)
