# Copyright (c) 2025, Gurudatt Kulkarni and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from gate_entry.setup.custom_fields import get_custom_fields
from gate_entry.setup.permissions import ensure_security_guard_permissions

REPORTS = (
	"pending_gate_passes",
	"gate_register",
	"material_reconciliation",
)


def after_install():
	"""
	Create custom fields after app installation
	Note: This may fail silently if ERPNext is not installed yet.
	Custom fields will be created when setup_custom_fields is run manually.
	"""
	try:
		create_gate_entry_custom_fields()
	except Exception as e:
		# Log error but don't fail installation if ERPNext doctypes don't exist yet
		frappe.log_error(
			message=f"Could not create custom fields during installation: {str(e)!r}\n"
			"This is expected if ERPNext is not installed yet. "
			"Run 'bench execute gate_entry.setup.setup_custom_fields.setup' after ERPNext is installed.",
			title="Gate Entry Installation - Custom Fields Skipped",
		)
		print(
			"Note: Custom fields not created. "
			"Run 'bench execute gate_entry.setup.setup_custom_fields.setup' after ERPNext is installed."
		)

	ensure_reports()
	ensure_security_guard_permissions()
	reload_gate_pass_doctype()
	ensure_workspace()


def after_migrate():
	"""Ensure reports and custom fields exist after migrations."""

	ensure_reports()
	ensure_security_guard_permissions()
	reload_gate_pass_doctype()
	ensure_workspace()


def create_gate_entry_custom_fields():
	"""
	Create custom fields in dependent ERPNext doctypes without touching core.
	"""
	custom_fields = get_custom_fields()
	create_custom_fields(custom_fields, update=True)
	# Manual commit in installation script to ensure fields are saved
	# nosemgrep
	frappe.db.commit()


def reload_gate_pass_doctype():
	"""Reload Gate Pass DocType so new metadata (e.g. Stock Entry links) is available."""
	try:
		frappe.reload_doc("gate_entry", "doctype", "gate_pass")
	except Exception as exc:
		frappe.log_error(
			message=f"Failed to reload Gate Pass DocType: {exc}",
			title="Gate Entry Gate Pass Reload",
		)


def ensure_reports():
	"""Reload script reports so they are available in the site."""

	for report in REPORTS:
		try:
			frappe.reload_doc("gate_entry", "report", report)
		except Exception as exc:
			frappe.log_error(
				message=f"Failed to reload Gate Entry report '{report}': {exc}",
				title="Gate Entry Report Reload",
			)
	# Commit so the reports are persisted when running bench migrate/execute
	# nosemgrep
	frappe.db.commit()


def ensure_workspace():
	"""Reload workspace so it is available in the sidebar."""
	try:
		frappe.reload_doc("gate_entry", "workspace", "gate_entry")
	except Exception as exc:
		frappe.log_error(
			message=f"Failed to reload Gate Entry workspace: {exc}",
			title="Gate Entry Workspace Reload",
		)
	# Commit so the workspace is persisted
	# nosemgrep
	frappe.db.commit()
