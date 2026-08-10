# Copyright (c) 2025, Gurudatt Kulkarni and contributors
# For license information, please see license.txt

"""
Provision Gate Entry custom fields on ERPNext doctypes.

Run this script using:
    bench execute gate_entry.setup.setup_custom_fields.setup
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from gate_entry.setup.custom_fields import get_custom_fields


def setup():
	"""
	Main entry point when executed via bench execute.
	"""
	create_gate_entry_custom_fields()
	print("Gate Entry custom fields created/updated successfully!")


def create_gate_entry_custom_fields():
	"""
	Create or update custom fields across dependent ERPNext doctypes.
	"""
	custom_fields = get_custom_fields()
	create_custom_fields(custom_fields, update=True)

	# Manual commit when executed via bench execute / during install.
	# nosemgrep
	frappe.db.commit()


if __name__ == "__main__":
	setup()
