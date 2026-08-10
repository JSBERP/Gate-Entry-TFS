import frappe
from frappe.utils import flt


def get_stock_entry_metadata(stock_entry_name):
	"""
	Fetch Stock Entry header data (type, posting_date, posting_time).
	"""
	if not stock_entry_name:
		return {}

	return (
		frappe.db.get_value(
			"Stock Entry",
			stock_entry_name,
			["stock_entry_type", "posting_date", "posting_time", "docstatus"],
			as_dict=True,
		)
		or {}
	)


def get_stock_entry_warehouses(stock_entry_name):
	"""
	Aggregate all source and target warehouses from Stock Entry items.
	Returns comma-separated strings for source and target warehouses.
	"""
	if not stock_entry_name:
		return "", ""

	items = frappe.get_all(
		"Stock Entry Detail", filters={"parent": stock_entry_name}, fields=["s_warehouse", "t_warehouse"]
	)

	source_warehouses = set()
	target_warehouses = set()

	for item in items:
		if item.s_warehouse:
			source_warehouses.add(item.s_warehouse)
		if item.t_warehouse:
			target_warehouses.add(item.t_warehouse)

	return ", ".join(sorted(source_warehouses)), ", ".join(sorted(target_warehouses))


def get_stock_entry_item_details(stock_entry_name):
	"""
	Fetch item-level details (item_code, item_name, quantity, uom) from Stock Entry.
	"""
	if not stock_entry_name:
		return []

	return frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": stock_entry_name},
		fields=["item_code", "item_name", "qty", "uom", "stock_uom", "transfer_qty"],
	)


def get_stock_entry_allocated_quantities(stock_entry_name):
	"""
	Calculate allocated quantities per item by summing Gate Pass quantities.
	Used for Material Reconciliation.
	"""
	if not stock_entry_name:
		return {}

	# Get all Gate Passes linked to this Stock Entry
	# Check both direct reference and return reference
	gate_passes = frappe.get_all(
		"Gate Pass",
		filters={"docstatus": 1, "document_reference": "Stock Entry", "reference_number": stock_entry_name},
		pluck="name",
	)

	# Also check outbound_material_transfer for return flows
	return_gate_passes = frappe.get_all(
		"Gate Pass", filters={"docstatus": 1, "outbound_material_transfer": stock_entry_name}, pluck="name"
	)

	all_gate_passes = set(gate_passes + return_gate_passes)

	if not all_gate_passes:
		return {}

	allocations = {}

	# Fetch items from all linked Gate Passes
	gp_items = frappe.get_all(
		"Gate Pass Table",
		filters={"parent": ["in", list(all_gate_passes)]},
		fields=["item_code", "received_qty", "dispatched_qty", "parent"],
	)

	for item in gp_items:
		# Determine direction based on Gate Pass type
		# We need to fetch entry_type for each gate pass to be precise,
		# or just sum both since one will be 0 typically based on direction
		qty = flt(item.received_qty) + flt(item.dispatched_qty)

		if item.item_code in allocations:
			allocations[item.item_code] += qty
		else:
			allocations[item.item_code] = qty

	return allocations


def is_stock_entry_cancelled(stock_entry_name):
	"""
	Check if Stock Entry is cancelled (docstatus = 2).
	"""
	if not stock_entry_name:
		return False

	docstatus = frappe.db.get_value("Stock Entry", stock_entry_name, "docstatus")
	return docstatus == 2
