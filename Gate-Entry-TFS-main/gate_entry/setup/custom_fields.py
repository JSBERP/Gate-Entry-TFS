# Copyright (c) 2025, Gurudatt Kulkarni and contributors
# For license information, please see license.txt

"""
Common custom field definitions for Gate Entry app.
These fields are added to ERPNext doctypes without modifying core files.
"""

PURCHASE_RECEIPT_FIELDS = [
	{
		"fieldname": "gate_pass",
		"label": "Gate Pass",
		"fieldtype": "Link",
		"options": "Gate Pass",
		"insert_after": "supplier_delivery_note",
		"read_only": 1,
		"no_copy": 1,
		"print_hide": 1,
		"translatable": 0,
		"search_index": 1,
	},
]

SUBCONTRACTING_RECEIPT_FIELDS = [
	{
		"fieldname": "gate_pass",
		"label": "Gate Pass",
		"fieldtype": "Link",
		"options": "Gate Pass",
		"insert_after": "supplier_delivery_note",
		"read_only": 1,
		"no_copy": 1,
		"print_hide": 1,
		"translatable": 0,
		"search_index": 1,
	},
]

STOCK_ENTRY_FIELDS = [
	{
		"fieldname": "ge_external_transfer",
		"label": "External Transfer",
		"fieldtype": "Check",
		"insert_after": "stock_entry_type",
		"depends_on": "eval:doc.stock_entry_type=='Material Transfer'",
		"default": "0",
		"translatable": 0,
		"description": "Mark when this transfer leaves the plant and requires a Gate Pass.",
	},
	{
		"fieldname": "ge_gate_pass_instruction",
		"label": "Gate Pass Instructions",
		"fieldtype": "Small Text",
		"insert_after": "ge_external_transfer",
		"depends_on": "eval:doc.ge_external_transfer==1",
		"translatable": 0,
		"description": "Optional notes for the gate/security team.",
	},
	{
		"fieldname": "gate_pass",
		"label": "Gate Pass",
		"fieldtype": "Link",
		"options": "Gate Pass",
		"insert_after": "inspection_required",
		"read_only": 1,
		"no_copy": 1,
		"translatable": 0,
		"description": "Gate Pass generated for this Stock Entry movement.",
	},
]


def get_custom_fields():
	"""
	Get all custom fields as a dictionary for use with create_custom_fields.

	Returns:
		dict: Dictionary mapping doctype names to their custom field definitions
	"""
	return {
		"Purchase Receipt": PURCHASE_RECEIPT_FIELDS,
		"Subcontracting Receipt": SUBCONTRACTING_RECEIPT_FIELDS,
		"Stock Entry": STOCK_ENTRY_FIELDS,
	}
