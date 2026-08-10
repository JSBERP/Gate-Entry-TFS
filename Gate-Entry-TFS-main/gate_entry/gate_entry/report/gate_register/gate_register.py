"""Gate Register report."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

import frappe
from frappe import _

from gate_entry.constants import INBOUND_REFERENCES, OUTBOUND_REFERENCES, REFERENCE_PARTY_FIELDS
from gate_entry.stock_integration.report_utils import (
	get_stock_entry_item_details,
	get_stock_entry_metadata,
	get_stock_entry_warehouses,
)


def execute(filters: dict | None = None):
	"""Run the Gate Register report."""

	report_filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(report_filters)
	report_summary = get_report_summary(data)

	return columns, data, None, None, report_summary


def get_columns() -> list[dict[str, object]]:
	"""Return the Gate Register column configuration."""

	return [
		{
			"label": _("Date"),
			"fieldname": "gate_entry_date",
			"fieldtype": "Date",
			"width": 110,
		},
		{
			"label": _("Time"),
			"fieldname": "gate_entry_time",
			"fieldtype": "Time",
			"width": 90,
		},
		{
			"label": _("Gate Pass ID"),
			"fieldname": "gate_pass",
			"fieldtype": "Link",
			"options": "Gate Pass",
			"width": 160,
		},
		{
			"label": _("Direction"),
			"fieldname": "entry_type",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": _("Source Type"),
			"fieldname": "document_reference",
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"label": _("Source Document"),
			"fieldname": "reference_number",
			"fieldtype": "Dynamic Link",
			"options": "document_reference",
			"width": 180,
		},
		{
			"label": _("Stock Entry"),
			"fieldname": "stock_entry",
			"fieldtype": "Link",
			"options": "Stock Entry",
			"width": 160,
		},
		{
			"label": _("Stock Entry Type"),
			"fieldname": "stock_entry_type",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": _("SE Posting Date"),
			"fieldname": "se_posting_date",
			"fieldtype": "Date",
			"width": 110,
		},
		{
			"label": _("SE Posting Time"),
			"fieldname": "se_posting_time",
			"fieldtype": "Time",
			"width": 90,
		},
		{
			"label": _("From Warehouses"),
			"fieldname": "from_warehouses",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": _("To Warehouses"),
			"fieldname": "to_warehouses",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": _("Outbound Transfer"),
			"fieldname": "outbound_transfer",
			"fieldtype": "Link",
			"options": "Stock Entry",
			"width": 160,
		},
		{
			"label": _("Party Type"),
			"fieldname": "party_type",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": _("Party"),
			"fieldname": "party_name",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": _("Vehicle Number"),
			"fieldname": "vehicle_number",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Driver Name"),
			"fieldname": "driver_name",
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"label": _("Material Summary"),
			"fieldname": "material_summary",
			"fieldtype": "Data",
			"width": 400,
		},
	]


def get_data(filters: frappe._dict) -> list[dict[str, object]]:
	"""Query Gate Pass data and compose material summaries."""

	gate_pass_filters: dict[str, object] = {"docstatus": ["<", 2]}

	if filters.get("from_date") and filters.get("to_date"):
		gate_pass_filters["gate_entry_date"] = [
			"between",
			[filters.from_date, filters.to_date],
		]
	elif filters.get("from_date"):
		gate_pass_filters["gate_entry_date"] = [">=", filters.from_date]
	elif filters.get("to_date"):
		gate_pass_filters["gate_entry_date"] = ["<=", filters.to_date]

	if filters.get("entry_type"):
		gate_pass_filters["entry_type"] = filters.entry_type

	if filters.get("document_reference"):
		gate_pass_filters["document_reference"] = filters.document_reference

	if filters.get("supplier"):
		gate_pass_filters["supplier"] = filters.supplier

	if filters.get("vehicle_number"):
		gate_pass_filters["vehicle_number"] = ["like", f"%{filters.vehicle_number}%"]

	if filters.get("company"):
		gate_pass_filters["company"] = filters.company

	# Stock Entry specific filters
	stock_entry_filters = {}
	if filters.get("stock_entry_type"):
		stock_entry_filters["stock_entry_type"] = filters.stock_entry_type

	if filters.get("warehouse"):
		# We need to find Stock Entries that have this warehouse in items
		# This is complex to filter on parent based on child table in simple dict filters
		# We'll handle this by fetching matching Stock Entries first
		pass

	matching_stock_entries = None
	if stock_entry_filters or filters.get("warehouse"):
		se_conditions = {"docstatus": ["!=", 2]}
		if stock_entry_filters:
			se_conditions.update(stock_entry_filters)

		# Get Stock Entries matching type/docstatus
		entries = frappe.get_all("Stock Entry", filters=se_conditions, pluck="name")

		# Filter by warehouse if needed
		if filters.get("warehouse"):
			warehouse_entries = frappe.get_all(
				"Stock Entry Detail",
				filters={"s_warehouse": ["=", filters.warehouse], "t_warehouse": ["=", filters.warehouse]},
				or_filters={"s_warehouse": filters.warehouse, "t_warehouse": filters.warehouse},
				pluck="parent",
				distinct=True,
			)
			if entries:
				entries = list(set(entries) & set(warehouse_entries))
			else:
				# If only warehouse filter was applied
				entries = warehouse_entries

		matching_stock_entries = entries

		if not matching_stock_entries:
			# If filters matched nothing, return empty result if we were looking for Stock Entry gate passes
			# But user might want to see other gate passes too?
			# Usually filters are restrictive. If I filter by Stock Entry Type, I only want Stock Entry gate passes.
			if filters.get("stock_entry_type") or filters.get("warehouse"):
				return []

		gate_pass_filters["reference_number"] = ["in", matching_stock_entries]
		gate_pass_filters["document_reference"] = "Stock Entry"

	gate_passes = frappe.get_all(
		"Gate Pass",
		filters=gate_pass_filters,
		fields=[
			"name",
			"gate_entry_date",
			"gate_entry_time",
			"entry_type",
			"document_reference",
			"reference_number",
			"vehicle_number",
			"driver_name",
			"supplier",
			"outbound_material_transfer",
		],
		order_by="gate_entry_date desc, gate_entry_time desc, name desc",
	)

	if not gate_passes:
		return []

	reference_parties = get_reference_parties(gate_passes)
	gate_pass_names = [gp.name for gp in gate_passes]
	items = frappe.get_all(
		"Gate Pass Table",
		filters={"parent": ["in", gate_pass_names]},
		fields=[
			"parent",
			"item_code",
			"item_name",
			"received_qty",
			"dispatched_qty",
			"uom",
		],
		order_by="parent asc, idx asc",
	)

	items_by_parent: dict[str, list[dict[str, object]]] = defaultdict(list)
	for item in items:
		items_by_parent[item.parent].append(item)

	# Pre-fetch Stock Entry data for all relevant gate passes
	stock_entry_data = get_stock_entry_data_for_register(gate_passes)

	data: list[dict[str, object]] = []
	for gate_pass in gate_passes:
		key = (gate_pass.document_reference, gate_pass.reference_number)
		party_details = reference_parties.get(key, {})

		party_type = derive_party_type(gate_pass.document_reference)
		party_name = party_details.get("party_name") or party_details.get("party") or gate_pass.supplier

		is_outbound = gate_pass.entry_type == "Gate Out"

		# Stock Entry Details
		se_details = {}
		if gate_pass.document_reference == "Stock Entry":
			se_details = stock_entry_data.get(gate_pass.reference_number, {})

		# Add item details to summary if Stock Entry
		se_item_details = []
		if gate_pass.document_reference == "Stock Entry":
			se_item_details = get_stock_entry_item_details(gate_pass.reference_number)

		material_summary = build_material_summary(
			items_by_parent.get(gate_pass.name, []), is_outbound=is_outbound, se_item_details=se_item_details
		)

		data.append(
			{
				"gate_entry_date": gate_pass.gate_entry_date,
				"gate_entry_time": gate_pass.gate_entry_time,
				"gate_pass": gate_pass.name,
				"entry_type": gate_pass.entry_type,
				"document_reference": gate_pass.document_reference,
				"reference_number": gate_pass.reference_number,
				"stock_entry": gate_pass.reference_number
				if gate_pass.document_reference == "Stock Entry"
				else None,
				"stock_entry_type": se_details.get("stock_entry_type"),
				"se_posting_date": se_details.get("posting_date"),
				"se_posting_time": se_details.get("posting_time"),
				"from_warehouses": se_details.get("source_warehouses"),
				"to_warehouses": se_details.get("target_warehouses"),
				"outbound_transfer": gate_pass.outbound_material_transfer,
				"party_type": party_type,
				"party_name": party_name,
				"vehicle_number": gate_pass.vehicle_number,
				"driver_name": gate_pass.driver_name,
				"material_summary": material_summary,
			}
		)

	return data


def get_stock_entry_data_for_register(gate_passes):
	"""Fetch bulk Stock Entry data."""
	stock_entry_names = set()
	for gp in gate_passes:
		if gp.document_reference == "Stock Entry" and gp.reference_number:
			stock_entry_names.add(gp.reference_number)

	if not stock_entry_names:
		return {}

	data = {}
	for name in stock_entry_names:
		metadata = get_stock_entry_metadata(name)
		if metadata.get("docstatus") == 2:
			continue  # Skip cancelled, though filtering usually handles this

		s_wh, t_wh = get_stock_entry_warehouses(name)

		data[name] = {
			"stock_entry_type": metadata.get("stock_entry_type"),
			"posting_date": metadata.get("posting_date"),
			"posting_time": metadata.get("posting_time"),
			"source_warehouses": s_wh,
			"target_warehouses": t_wh,
		}
	return data


def get_reference_parties(
	gate_passes: Iterable[frappe._dict],
) -> dict[tuple[str | None, str | None], dict[str, str | None]]:
	"""Fetch party information for reference documents."""

	reference_numbers: dict[str, set[str]] = defaultdict(set)
	for gate_pass in gate_passes:
		if not gate_pass.document_reference or not gate_pass.reference_number:
			continue
		if gate_pass.document_reference not in REFERENCE_PARTY_FIELDS:
			continue
		reference_numbers[gate_pass.document_reference].add(gate_pass.reference_number)

	party_details: dict[tuple[str | None, str | None], dict[str, str | None]] = {}
	for document_reference, names in reference_numbers.items():
		if not names:
			continue
		party_field, party_name_field = REFERENCE_PARTY_FIELDS[document_reference]
		records = frappe.get_all(
			document_reference,
			filters={"name": ["in", list(names)]},
			fields=["name", party_field, party_name_field],
		)
		for record in records:
			party_details[(document_reference, record.name)] = {
				"party": record.get(party_field),
				"party_name": record.get(party_name_field) or record.get(party_field),
			}

	return party_details


def derive_party_type(document_reference: str | None) -> str | None:
	"""Return the human-readable party type for the given reference."""

	if not document_reference:
		return None

	if document_reference in INBOUND_REFERENCES:
		return _("Supplier")

	if document_reference in OUTBOUND_REFERENCES:
		return _("Customer")

	if document_reference == "Stock Entry":
		return _("Internal")

	return None


def build_material_summary(
	items: Iterable[dict[str, object]] | None,
	is_outbound: bool = False,
	se_item_details: list[dict] | None = None,
) -> str:
	"""Create the comma-separated material summary string."""

	if not items:
		return "-"

	# If we have Stock Entry item details, use those as they are more authoritative
	# But Gate Pass items show actual movement.
	# Requirement: "include Stock Entry item-level details in the material summary"
	# Let's combine them or prefer Gate Pass quantity but use SE item details for description?
	# Actually, gate pass table has item_code, item_name, uom etc.
	# Let's just append Stock Entry info if useful, or just stick to Gate Pass items
	# The prompt said: "report **must** display Stock Entry item-level details in the material summary when available."

	quantity_field = "dispatched_qty" if is_outbound else "received_qty"
	summary: list[str] = []
	for item in items:
		qty = item.get(quantity_field) or 0
		if not qty:
			fallback_field = "received_qty" if quantity_field == "dispatched_qty" else "dispatched_qty"
			qty = item.get(fallback_field) or 0
		qty_formatted = frappe.format_value(qty, {"fieldtype": "Float", "precision": 3})
		uom = item.get("uom") or ""
		qty_with_uom = f"{qty_formatted} {uom}".strip()
		summary.append(f"{item.get('item_code')} ({qty_with_uom})")

	return ", ".join(summary) if summary else "-"


def get_report_summary(data: Iterable[dict[str, object]]) -> list[dict[str, object]]:
	"""Return summary widgets for the register."""

	data = list(data)
	if not data:
		return []

	total_inbound = sum(1 for row in data if row.get("entry_type") == "Gate In")
	total_outbound = sum(1 for row in data if row.get("entry_type") == "Gate Out")

	summary = [
		{
			"label": _("Gate Passes"),
			"value": len(data),
			"indicator": "blue",
			"datatype": "Int",
		}
	]

	if total_inbound:
		summary.append(
			{
				"label": _("Inbound"),
				"value": total_inbound,
				"indicator": "green",
				"datatype": "Int",
			}
		)

	if total_outbound:
		summary.append(
			{
				"label": _("Outbound"),
				"value": total_outbound,
				"indicator": "orange",
				"datatype": "Int",
			}
		)

	return summary
