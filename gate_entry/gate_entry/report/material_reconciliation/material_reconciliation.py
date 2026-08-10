"""Material Reconciliation report."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import NamedTuple

import frappe
from frappe import _
from frappe.utils import flt

from gate_entry.constants import (
	ALL_REFERENCES,
	INBOUND_REFERENCES,
	OUTBOUND_REFERENCES,
	REFERENCE_PARTY_FIELDS,
)
from gate_entry.stock_integration.report_utils import (
	get_stock_entry_allocated_quantities,
	get_stock_entry_item_details,
	get_stock_entry_metadata,
	get_stock_entry_warehouses,
	is_stock_entry_cancelled,
)

SUPPORTED_DOCUMENT_REFERENCES = ALL_REFERENCES | {"Stock Entry"}


class Key(NamedTuple):
	"""Composite key used for aggregations."""

	document_reference: str
	reference_number: str
	item_code: str


def execute(filters: dict | None = None):
	"""Run the Material Reconciliation report."""

	report_filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(report_filters)
	report_summary = get_report_summary(data)

	return columns, data, None, None, report_summary


def get_columns() -> list[dict[str, object]]:
	"""Define report columns."""

	return [
		{
			"label": _("Direction"),
			"fieldname": "direction",
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"label": _("Document Type"),
			"fieldname": "document_reference",
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"label": _("Reference Document"),
			"fieldname": "reference_label",
			"fieldtype": "Data",
			"width": 220,
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
			"label": _("Warehouses"),
			"fieldname": "warehouses",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": _("Party"),
			"fieldname": "party_name",
			"fieldtype": "Data",
			"width": 220,
		},
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 150,
		},
		{
			"label": _("Item Name"),
			"fieldname": "item_name",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": _("Gate Pass Qty"),
			"fieldname": "gate_pass_qty",
			"fieldtype": "Float",
			"width": 140,
		},
		{
			"label": _("Reference Qty"),
			"fieldname": "receipt_qty",
			"fieldtype": "Float",
			"width": 140,
		},
		{
			"label": _("Discrepancy"),
			"fieldname": "discrepancy",
			"fieldtype": "Float",
			"width": 140,
		},
	]


def get_data(filters: frappe._dict) -> list[dict[str, object]]:
	"""Collect and merge data from Gate Passes and Receipts."""

	document_reference_filter = normalise_document_type(filters.get("document_type"))

	gate_pass_map = get_gate_pass_totals(filters, document_reference_filter)
	receipt_map = get_receipt_totals(filters, document_reference_filter)

	keys = set(gate_pass_map) | set(receipt_map)
	if not keys:
		return []

	reference_parties = get_reference_parties(keys)
	item_name_cache: dict[str, str] = {}

	data: list[dict[str, object]] = []
	for key in sorted(keys):
		# Exclude cancelled Stock Entries
		if key.document_reference == "Stock Entry" and is_stock_entry_cancelled(key.reference_number):
			continue

		gate_pass_row = gate_pass_map.get(key)
		receipt_row = receipt_map.get(key)

		gate_pass_qty = flt(gate_pass_row.get("gate_pass_qty") if gate_pass_row else 0)
		receipt_qty = flt(receipt_row.get("receipt_qty") if receipt_row else 0)
		discrepancy = gate_pass_qty - receipt_qty

		party_details = reference_parties.get((key.document_reference, key.reference_number), {})
		party_id = party_details.get("party")
		party_name = party_details.get("party_name") or party_id or ""

		if filters.get("customer"):
			if key.document_reference in INBOUND_REFERENCES:
				continue
			if party_id != filters.customer:
				continue

		if filters.get("supplier") and key.document_reference in OUTBOUND_REFERENCES:
			continue

		# Filters for Stock Entry
		if key.document_reference == "Stock Entry":
			if filters.get("stock_entry") and key.reference_number != filters.stock_entry:
				continue
			# Type filter is tricky as we need to fetch type.
			# We will fetch metadata below and filter then.

		item_name = determine_item_name(gate_pass_row, receipt_row, item_name_cache)
		direction = determine_direction(gate_pass_row, key.document_reference)

		# Enrich with Stock Entry data
		se_link = None
		se_type = None
		se_warehouses = None

		if key.document_reference == "Stock Entry":
			se_link = key.reference_number
			meta = get_stock_entry_metadata(se_link)
			se_type = meta.get("stock_entry_type")

			if filters.get("stock_entry_type") and se_type != filters.stock_entry_type:
				continue

			s_wh, t_wh = get_stock_entry_warehouses(se_link)
			warehouses = []
			if s_wh:
				warehouses.append(f"Src: {s_wh}")
			if t_wh:
				warehouses.append(f"Tgt: {t_wh}")
			se_warehouses = ", ".join(warehouses)

			# Also use item details from Stock Entry if name is missing
			if not item_name:
				item_details = get_stock_entry_item_details(se_link)
				for item in item_details:
					if item.item_code == key.item_code:
						item_name = item.item_name
						item_name_cache[key.item_code] = item_name
						break

		data.append(
			{
				"direction": direction,
				"document_reference": key.document_reference,
				"reference_label": format_reference_label(key.document_reference, key.reference_number),
				"reference_number": key.reference_number,
				"stock_entry": se_link,
				"stock_entry_type": se_type,
				"warehouses": se_warehouses,
				"party_name": party_name,
				"item_code": key.item_code,
				"item_name": item_name,
				"gate_pass_qty": gate_pass_qty,
				"receipt_qty": receipt_qty,
				"discrepancy": discrepancy,
				"has_discrepancy": abs(discrepancy) > 1e-6,
			}
		)

	return data


def normalise_document_type(value: str | None) -> str | None:
	"""Return the canonical document reference value."""

	if not value or value in {"All", ""}:
		return None

	if value in SUPPORTED_DOCUMENT_REFERENCES:
		return value

	frappe.throw(_("Unsupported document type filter: {0}").format(value))
	return None


def get_gate_pass_totals(filters: frappe._dict, document_reference_filter: str | None):
	"""Aggregate Gate Pass quantities.

	For inter-company Stock Entry transfers the Gate Pass may be submitted
	under the *receiving* company while the referenced Stock Entry belongs to
	the *sending* company.  When the user filters by company we therefore:

	  1. Include Gate Passes directly owned by the filtered company (standard).
	  2. Include Gate Passes (Gate In, Stock Entry type) whose *referenced*
	     Stock Entry belongs to the filtered company so that the sending
	     company's reconciliation report shows 0 discrepancy as well.
	"""

	conditions = ["gp.docstatus = 1"]
	values: dict[str, object] = {}

	if document_reference_filter:
		conditions.append("gp.document_reference = %(document_reference)s")
		values["document_reference"] = document_reference_filter
	else:
		allowed_references = "', '".join(sorted(SUPPORTED_DOCUMENT_REFERENCES))
		conditions.append(f"gp.document_reference in ('{allowed_references}')")

	if filters.get("from_date"):
		conditions.append("gp.gate_pass_date >= %(from_date)s")
		values["from_date"] = filters.from_date

	if filters.get("to_date"):
		conditions.append("gp.gate_pass_date <= %(to_date)s")
		values["to_date"] = filters.to_date

	if filters.get("supplier"):
		conditions.append("gp.supplier = %(supplier)s")
		values["supplier"] = filters.supplier

	if filters.get("company"):
		# Match gate passes directly under this company OR inter-company gate passes
		# whose referenced Stock Entry belongs to this company.
		conditions.append(
			"(gp.company = %(company)s OR ("
			"  gp.document_reference = 'Stock Entry'"
			"  AND gp.entry_type = 'Gate In'"
			"  AND EXISTS ("
			"    SELECT 1 FROM `tabStock Entry` se"
			"    WHERE se.name = gp.reference_number"
			"    AND se.company = %(company)s"
			"  )"
			"))"
		)
		values["company"] = filters.company

	# For Stock Entry, we might want to filter by reference number in main query if passed
	if filters.get("stock_entry"):
		# This optimizes if filtering by specific stock entry
		conditions.append("gp.reference_number = %(stock_entry)s")
		values["stock_entry"] = filters.stock_entry

	query = f"""
        SELECT
            gp.document_reference,
            gp.reference_number,
            MAX(gp.entry_type) AS entry_type,
            gpit.item_code,
            gpit.item_name,
            SUM(
                CASE
                    WHEN gp.entry_type = 'Gate Out' THEN IFNULL(gpit.dispatched_qty, 0)
                    ELSE IFNULL(gpit.received_qty, 0)
                END
            ) AS gate_pass_qty
        FROM `tabGate Pass` gp
        JOIN `tabGate Pass Table` gpit ON gpit.parent = gp.name
        WHERE {' AND '.join(conditions)}
        GROUP BY gp.document_reference, gp.reference_number, gpit.item_code
    """

	results = frappe.db.sql(query, values, as_dict=True)

	gate_pass_map: dict[Key, dict[str, object]] = {}
	for row in results:
		key = Key(row.document_reference, row.reference_number, row.item_code)
		gate_pass_map[key] = row

	return gate_pass_map


def get_receipt_totals(filters: frappe._dict, document_reference_filter: str | None):
	"""Aggregate quantities from downstream documents."""

	receipt_map: dict[Key, dict[str, object]] = {}

	if document_reference_filter in (None, "Purchase Order"):
		receipt_map.update(get_purchase_receipt_totals(filters))

	if document_reference_filter in (None, "Subcontracting Order"):
		receipt_map.update(get_subcontracting_receipt_totals(filters))

	if document_reference_filter in (None, "Sales Invoice"):
		receipt_map.update(get_sales_invoice_totals(filters))

	if document_reference_filter in (None, "Delivery Note"):
		receipt_map.update(get_delivery_note_totals(filters))

	if document_reference_filter in (None, "Stock Entry"):
		receipt_map.update(get_stock_entry_totals(filters))

	return receipt_map


def get_stock_entry_totals(filters: frappe._dict):
	"""Aggregate Stock Entry allocated totals."""

	if filters.get("customer") or filters.get("supplier"):
		# Stock Entries don't really map to these directly in the same way
		return {}

	# Strategy:
	# We need keys (Stock Entry, Item Code) -> Quantity
	# We can iterate through all relevant Stock Entries found in Gate Passes
	# But we need to support cases where we want to see Stock Entries regardless?
	# No, this report is "Material Reconciliation", usually reconciling Gate Pass vs Doc.
	# So we only care about Stock Entries that HAVE gate passes (which get_gate_pass_totals finds).
	# OR we find all Stock Entries in date range.
	# But "Allocated Qty" is defined as "Sum of Gate Pass Qtys".
	# So we can just query Gate Passes again or reuse logic.

	# Efficient way:
	# 1. Get relevant Stock Entry names from filters (date range etc) OR from the gate_pass_map keys if we had access.
	# Since we don't have gate_pass_map here, we query.

	# Actually, reusing the logic in `get_stock_entry_allocated_quantities` utility is good but doing it one by one is slow.
	# We should implement a bulk fetch here similar to other `get_..._totals`.

	conditions = ["gp.docstatus = 1", "gp.document_reference = 'Stock Entry'"]
	values: dict[str, object] = {}

	if filters.get("from_date"):
		conditions.append("gp.gate_pass_date >= %(from_date)s")
		values["from_date"] = filters.from_date

	if filters.get("to_date"):
		conditions.append("gp.gate_pass_date <= %(to_date)s")
		values["to_date"] = filters.to_date

	if filters.get("stock_entry"):
		conditions.append("gp.reference_number = %(stock_entry)s")
		values["stock_entry"] = filters.stock_entry

	# Include return transfers?
	# "The report must handle return material transfers by comparing return Stock Entry allocated quantities with inbound Gate Pass quantities."
	# For manual return flow, `reference_number` is the Outbound Stock Entry initially, then becomes Return Stock Entry?
	# If `reference_number` points to the Stock Entry we are reconciling against, then the query below works.

	query = f"""
        SELECT
            gp.reference_number,
            gpit.item_code,
            MAX(gpit.item_name) as item_name,
            SUM(
                CASE
                    WHEN gp.entry_type = 'Gate Out' THEN IFNULL(gpit.dispatched_qty, 0)
                    ELSE IFNULL(gpit.received_qty, 0)
                END
            ) AS receipt_qty
        FROM `tabGate Pass` gp
        JOIN `tabGate Pass Table` gpit ON gpit.parent = gp.name
        WHERE {' AND '.join(conditions)}
        GROUP BY gp.reference_number, gpit.item_code
    """

	results = frappe.db.sql(query, values, as_dict=True)

	receipt_map: dict[Key, dict[str, object]] = {}
	for row in results:
		if not row.reference_number:
			continue
		key = Key("Stock Entry", row.reference_number, row.item_code)
		receipt_map[key] = row

	return receipt_map


def get_purchase_receipt_totals(filters: frappe._dict):
	"""Aggregate Purchase Receipt quantities keyed by Purchase Order and Item."""

	if filters.get("customer"):
		return {}

	conditions = ["pr.docstatus = 1", "pri.purchase_order is not null", "pr.subcontracting_receipt is null"]
	values: dict[str, object] = {}

	if filters.get("from_date"):
		conditions.append("pr.posting_date >= %(pr_from_date)s")
		values["pr_from_date"] = filters.from_date

	if filters.get("to_date"):
		conditions.append("pr.posting_date <= %(pr_to_date)s")
		values["pr_to_date"] = filters.to_date

	if filters.get("supplier"):
		conditions.append("pr.supplier = %(pr_supplier)s")
		values["pr_supplier"] = filters.supplier

	if filters.get("company"):
		conditions.append("pr.company = %(pr_company)s")
		values["pr_company"] = filters.company

	query = f"""
        SELECT
            pri.purchase_order AS reference_number,
            pri.item_code,
            IFNULL(pri.item_name, '') AS item_name,
            SUM(pri.qty) AS receipt_qty
        FROM `tabPurchase Receipt Item` pri
        JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
        WHERE {' AND '.join(conditions)}
        GROUP BY pri.purchase_order, pri.item_code
    """

	results = frappe.db.sql(query, values, as_dict=True)

	receipt_map: dict[Key, dict[str, object]] = {}
	for row in results:
		key = Key("Purchase Order", row.reference_number, row.item_code)
		receipt_map[key] = row

	return receipt_map


def get_subcontracting_receipt_totals(filters: frappe._dict):
	"""Aggregate Subcontracting Receipt quantities keyed by Subcontracting Order and Item."""

	if filters.get("customer"):
		return {}

	conditions = ["sr.docstatus = 1", "sri.subcontracting_order is not null"]
	values: dict[str, object] = {}

	if filters.get("from_date"):
		conditions.append("sr.posting_date >= %(sr_from_date)s")
		values["sr_from_date"] = filters.from_date

	if filters.get("to_date"):
		conditions.append("sr.posting_date <= %(sr_to_date)s")
		values["sr_to_date"] = filters.to_date

	if filters.get("supplier"):
		conditions.append("sr.supplier = %(sr_supplier)s")
		values["sr_supplier"] = filters.supplier

	if filters.get("company"):
		conditions.append("sr.company = %(sr_company)s")
		values["sr_company"] = filters.company

	query = f"""
        SELECT
            sri.subcontracting_order AS reference_number,
            sri.item_code,
            IFNULL(sri.item_name, '') AS item_name,
            SUM(sri.qty) AS receipt_qty
        FROM `tabSubcontracting Receipt Item` sri
        JOIN `tabSubcontracting Receipt` sr ON sr.name = sri.parent
        WHERE {' AND '.join(conditions)}
        GROUP BY sri.subcontracting_order, sri.item_code
    """

	results = frappe.db.sql(query, values, as_dict=True)

	receipt_map: dict[Key, dict[str, object]] = {}
	for row in results:
		key = Key("Subcontracting Order", row.reference_number, row.item_code)
		receipt_map[key] = row

	return receipt_map


def get_sales_invoice_totals(filters: frappe._dict):
	"""Aggregate Sales Invoice quantities keyed by Sales Invoice and Item."""

	if filters.get("supplier"):
		return {}

	conditions = ["si.docstatus = 1"]
	values: dict[str, object] = {}

	if filters.get("from_date"):
		conditions.append("si.posting_date >= %(si_from_date)s")
		values["si_from_date"] = filters.from_date

	if filters.get("to_date"):
		conditions.append("si.posting_date <= %(si_to_date)s")
		values["si_to_date"] = filters.to_date

	if filters.get("customer"):
		conditions.append("si.customer = %(si_customer)s")
		values["si_customer"] = filters.customer

	if filters.get("company"):
		conditions.append("si.company = %(si_company)s")
		values["si_company"] = filters.company

	query = f"""
        SELECT
            sii.parent AS reference_number,
            sii.item_code,
            IFNULL(sii.item_name, '') AS item_name,
            SUM(sii.qty) AS receipt_qty
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE {' AND '.join(conditions)}
        GROUP BY sii.parent, sii.item_code
    """

	results = frappe.db.sql(query, values, as_dict=True)

	receipt_map: dict[Key, dict[str, object]] = {}
	for row in results:
		key = Key("Sales Invoice", row.reference_number, row.item_code)
		receipt_map[key] = row

	return receipt_map


def get_delivery_note_totals(filters: frappe._dict):
	"""Aggregate Delivery Note quantities keyed by Delivery Note and Item."""

	if filters.get("supplier"):
		return {}

	conditions = ["dn.docstatus = 1"]
	values: dict[str, object] = {}

	if filters.get("from_date"):
		conditions.append("dn.posting_date >= %(dn_from_date)s")
		values["dn_from_date"] = filters.from_date

	if filters.get("to_date"):
		conditions.append("dn.posting_date <= %(dn_to_date)s")
		values["dn_to_date"] = filters.to_date

	if filters.get("customer"):
		conditions.append("dn.customer = %(dn_customer)s")
		values["dn_customer"] = filters.customer

	if filters.get("company"):
		conditions.append("dn.company = %(dn_company)s")
		values["dn_company"] = filters.company

	query = f"""
        SELECT
            dni.parent AS reference_number,
            dni.item_code,
            IFNULL(dni.item_name, '') AS item_name,
            SUM(dni.qty) AS receipt_qty
        FROM `tabDelivery Note Item` dni
        JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE {' AND '.join(conditions)}
        GROUP BY dni.parent, dni.item_code
    """

	results = frappe.db.sql(query, values, as_dict=True)

	receipt_map: dict[Key, dict[str, object]] = {}
	for row in results:
		key = Key("Delivery Note", row.reference_number, row.item_code)
		receipt_map[key] = row

	return receipt_map


def get_reference_parties(keys: Iterable[Key]):
	"""Fetch party details for reference documents."""

	reference_numbers: dict[str, set[str]] = defaultdict(set)
	for key in keys:
		if key.document_reference not in REFERENCE_PARTY_FIELDS:
			continue
		reference_numbers[key.document_reference].add(key.reference_number)

	party_details: dict[tuple[str, str], dict[str, str | None]] = {}
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
			party = record.get(party_field)
			party_name = record.get(party_name_field) or party
			party_details[(document_reference, record.name)] = {
				"party": party,
				"party_name": party_name,
			}

	return party_details


def determine_direction(gate_pass_row: dict[str, object] | None, document_reference: str | None) -> str:
	"""Determine movement direction for the reconciliation row."""

	if gate_pass_row and gate_pass_row.get("entry_type"):
		return gate_pass_row.get("entry_type")

	if document_reference in OUTBOUND_REFERENCES:
		return "Gate Out"

	if document_reference in INBOUND_REFERENCES:
		return "Gate In"

	if document_reference == "Stock Entry":
		# We can't determine easily without row data, assume Gate Out default or mixed?
		# Actually, gate_pass_row usually exists if there's data.
		pass

	return ""


def determine_item_name(
	gate_pass_row: dict[str, object] | None,
	receipt_row: dict[str, object] | None,
	cache: dict[str, str],
) -> str:
	"""Resolve an item name from available sources, with caching."""

	if gate_pass_row and gate_pass_row.get("item_name"):
		return gate_pass_row.get("item_name")

	if receipt_row and receipt_row.get("item_name"):
		return receipt_row.get("item_name")

	item_code = None
	if gate_pass_row and gate_pass_row.get("item_code"):
		item_code = gate_pass_row.get("item_code")
	elif receipt_row and receipt_row.get("item_code"):
		item_code = receipt_row.get("item_code")

	if not item_code:
		return ""

	if item_code not in cache:
		cache[item_code] = frappe.db.get_value("Item", item_code, "item_name") or ""

	return cache[item_code]


def get_report_summary(data: Iterable[dict[str, object]]) -> list[dict[str, object]]:
	"""Build summary indicators for the reconciliation report."""

	data = list(data)
	if not data:
		return []

	total_gate_pass_qty = sum(flt(row.get("gate_pass_qty"), 6) for row in data)
	total_receipt_qty = sum(flt(row.get("receipt_qty"), 6) for row in data)
	total_discrepancy = sum(flt(row.get("discrepancy"), 6) for row in data)

	indicator = "red" if abs(total_discrepancy) > 1e-6 else "green"

	return [
		{
			"label": _("Total Gate Pass Qty"),
			"value": total_gate_pass_qty,
			"indicator": "blue",
			"datatype": "Float",
		},
		{
			"label": _("Total Receipt Qty"),
			"value": total_receipt_qty,
			"indicator": "green",
			"datatype": "Float",
		},
		{
			"label": _("Total Discrepancy"),
			"value": total_discrepancy,
			"indicator": indicator,
			"datatype": "Float",
		},
	]


def format_reference_label(document_reference: str, reference_number: str) -> str:
	"""Format the PO/SO label shown in the report."""

	if document_reference == "Purchase Order":
		prefix = _("PO")
	elif document_reference == "Subcontracting Order":
		prefix = _("SO")
	elif document_reference == "Sales Invoice":
		prefix = _("SI")
	elif document_reference == "Delivery Note":
		prefix = _("DN")
	elif document_reference == "Stock Entry":
		prefix = _("SE")
	else:
		prefix = document_reference

	return f"{reference_number} ({prefix})"
