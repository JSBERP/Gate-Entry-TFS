# Copyright (c) 2025, Gurudatt Kulkarni and contributors
# For license information, please see license.txt

"""
Uninstall hooks for Gate Entry app.
Removes custom fields from ERPNext doctypes to restore them to original state.
"""

import frappe


def before_uninstall():
	"""
	Remove custom fields before uninstalling the app.
	This must run before the Gate Pass doctype is deleted.
	"""
	try:
		delete_gate_entry_custom_fields()
	except Exception as e:
		# Log error but don't fail uninstallation
		frappe.log_error(
			message=f"Could not delete custom fields during uninstallation: {str(e)!r}\n"
			"Custom fields may need to be deleted manually.",
			title="Gate Entry Uninstallation - Custom Fields Cleanup",
		)
		print(f"Warning: Could not delete all custom fields: {e}")


def after_uninstall():
	"""
	Final cleanup after app uninstallation.
	"""
	# Clear any cached metadata
	frappe.clear_cache()


def delete_gate_entry_custom_fields():
	"""
	Delete all custom fields added by Gate Entry app from ERPNext doctypes.
	This restores the doctypes to their original state.
	"""
	# List of all custom fields to delete
	# Format: (doctype, fieldname)
	# Note: Including both "gate_pass" and "ge_gate_pass" for Stock Entry
	# to handle any legacy installations that may have used the wrong fieldname
	custom_fields_to_delete = [
		("Purchase Receipt", "gate_pass"),
		("Subcontracting Receipt", "gate_pass"),
		("Stock Entry", "ge_external_transfer"),
		("Stock Entry", "ge_gate_pass_instruction"),
		("Stock Entry", "gate_pass"),  # Legacy fieldname, if it exists
	]

	deleted_count = 0
	for doctype, fieldname in custom_fields_to_delete:
		try:
			# Check if the custom field exists
			custom_field_name = f"{doctype}-{fieldname}"
			if frappe.db.exists("Custom Field", custom_field_name):
				frappe.delete_doc("Custom Field", custom_field_name, force=1, ignore_permissions=True)
				deleted_count += 1
				print(f"Deleted custom field: {doctype}.{fieldname}")
		except Exception as e:
			# Log error but continue with other fields
			frappe.log_error(
				message=f"Failed to delete custom field {doctype}.{fieldname}: {str(e)!r}",
				title="Gate Entry Uninstall - Field Deletion Error",
			)
			print(f"Warning: Could not delete {doctype}.{fieldname}: {e}")

	# Commit the deletions
	# Manual commit required: Uninstall hooks run outside normal transaction context
	# and need explicit commits to persist changes before app removal
	if deleted_count > 0:
		frappe.db.commit()  # nosemgrep
		print(f"Successfully deleted {deleted_count} custom field(s).")
	else:
		print("No custom fields found to delete.")
