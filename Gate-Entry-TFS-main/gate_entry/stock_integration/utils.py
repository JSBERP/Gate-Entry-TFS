from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint


def is_material_transfer(stock_entry) -> bool:
	return getattr(stock_entry, "stock_entry_type", None) == "Material Transfer"


def is_send_to_subcontractor(stock_entry) -> bool:
	return getattr(stock_entry, "stock_entry_type", None) == "Send to Subcontractor"


def is_external_transfer(stock_entry) -> bool:
	return cint(getattr(stock_entry, "ge_external_transfer", 0)) == 1


def is_return_entry(stock_entry) -> bool:
	return cint(getattr(stock_entry, "is_return", 0)) == 1


def get_stock_entry_receiver_company(stock_entry) -> str | None:
	for fn in ("transfer_to_company", "custom_transfer_to_company"):
		val = (getattr(stock_entry, fn, None) or "").strip()
		if val:
			return val
	if (getattr(stock_entry, "party_type", None) or "").strip() == "Company":
		party = (getattr(stock_entry, "party", None) or "").strip()
		if party:
			return party
	return None


def is_job_work_intercompany_stock_entry(stock_entry) -> bool:
	"""True when STE is Material Transfer between Thusma 1Z0 and JSB 1ZT/2ZS."""
	from gate_entry.constants import JSB_JOB_WORK_COMPANIES, THUSMA_JOB_WORK_COMPANY

	sender = (getattr(stock_entry, "company", None) or "").strip()
	receiver = get_stock_entry_receiver_company(stock_entry)
	if not sender or not receiver or sender == receiver:
		return False
	participants = {THUSMA_JOB_WORK_COMPANY, *JSB_JOB_WORK_COMPANIES}
	return sender in participants and receiver in participants


def get_original_outbound_transfer(stock_entry):
	if is_return_entry(stock_entry):
		return getattr(stock_entry, "return_against", None)
	return getattr(stock_entry, "ge_outbound_reference", None)


def get_linked_return_transfer(stock_entry):
	return getattr(stock_entry, "ge_linked_return_transfer", None)


@frappe.whitelist()
def get_outbound_transfer_reference(stock_entry_name):
	"""
	Get the outbound transfer reference for a return Stock Entry.
	Checks doc_references table first, then falls back to return_against field.

	Args:
		stock_entry_name: Name of the Stock Entry

	Returns:
		Name of the outbound Stock Entry if this is a return entry, None otherwise
	"""
	if not stock_entry_name:
		return None

	try:
		stock_entry = frappe.get_doc("Stock Entry", stock_entry_name)

		# First check doc_references table for Stock Entry references
		if hasattr(stock_entry, "doc_references") and stock_entry.doc_references:
			for ref in stock_entry.doc_references:
				if ref.link_doctype == "Stock Entry" and ref.link_name:
					return ref.link_name

		# Fall back to return_against field if doc_references doesn't have it
		return get_original_outbound_transfer(stock_entry)
	except frappe.DoesNotExistError:
		return None


@frappe.whitelist()
def get_gate_pass_status(stock_entry_name):
	"""
	Get the Gate Pass status for a Stock Entry.

	Args:
		stock_entry_name: Name of the Stock Entry

	Returns:
		Dict with 'exists' (bool), 'name' (str or None), and 'docstatus' (int or None)
	"""
	if not stock_entry_name:
		return {"exists": False, "name": None, "docstatus": None}

	try:
		stock_entry = frappe.get_doc("Stock Entry", stock_entry_name)

		# Check gate_pass field first
		gate_pass_name = getattr(stock_entry, "gate_pass", None)

		# For return entries, also check return_material_transfer field
		if not gate_pass_name and is_return_entry(stock_entry):
			gate_passes = frappe.get_all(
				"Gate Pass",
				filters={
					"document_reference": "Stock Entry",
					"return_material_transfer": stock_entry_name,
				},
				fields=["name", "docstatus"],
				limit=1,
			)
			if gate_passes:
				gate_pass_name = gate_passes[0].name
				docstatus = gate_passes[0].docstatus
			else:
				return {"exists": False, "name": None, "docstatus": None}
		else:
			# Get docstatus if gate_pass field exists
			if gate_pass_name:
				docstatus = frappe.db.get_value("Gate Pass", gate_pass_name, "docstatus")
			else:
				return {"exists": False, "name": None, "docstatus": None}

		return {
			"exists": True,
			"name": gate_pass_name,
			"docstatus": docstatus,
		}
	except frappe.DoesNotExistError:
		return {"exists": False, "name": None, "docstatus": None}


def create_gate_pass_from_stock_entry(stock_entry_name: str, enqueued_by: str | None = None):
	stock_entry = frappe.get_doc("Stock Entry", stock_entry_name)

	if stock_entry.docstatus != 1:
		return

	if is_material_transfer(stock_entry) and not is_external_transfer(stock_entry):
		is_intercompany = False
		transfer_to_company = getattr(stock_entry, "transfer_to_company", None)
		party = getattr(stock_entry, "party", None)
		
		if transfer_to_company and transfer_to_company != stock_entry.company:
			is_intercompany = True
		elif party and party != stock_entry.company:
			is_intercompany = True
		else:
			for item in stock_entry.items:
				if item.t_warehouse:
					wh_company = frappe.db.get_value("Warehouse", item.t_warehouse, "company")
					if wh_company and wh_company != stock_entry.company:
						is_intercompany = True
						break
		if not is_intercompany:
			return

	is_return = is_return_entry(stock_entry)

	if is_return:
		existing = frappe.get_all(
			"Gate Pass",
			filters={
				"document_reference": "Stock Entry",
				"entry_type": "Gate In",
				"docstatus": 0,
				"outbound_material_transfer": stock_entry.return_against,
			},
			pluck="name",
		)

		if existing:
			gate_pass = frappe.get_doc("Gate Pass", existing[0])
			gate_pass.reference_number = stock_entry.name
			gate_pass.manual_return_flow = 0
			gate_pass.return_material_transfer = stock_entry.name
			gate_pass.stock_entry = stock_entry.name
			gate_pass.document_reference = "Stock Entry"
			gate_pass.entry_type = "Gate In"

			gate_pass.populate_gate_pass_items(gate_pass.get_stock_entry_items(stock_entry))
			gate_pass.save(ignore_permissions=True)

			# Manual commit required: This function runs in background queue (enqueued)
			# and needs explicit commit to persist gate pass updates before returning
			frappe.db.commit()  # nosemgrep
			return

	# --- Check for existing Gate Pass for this company (allow one per company) ---
	def has_existing_gate_pass(company):
		return bool(
			frappe.get_all(
				"Gate Pass",
				filters={
					"document_reference": "Stock Entry",
					"reference_number": stock_entry.name,
					"company": company,
				},
				pluck="name",
			)
		)

	# --- Create outbound Gate Pass for the stock entry's own company (e.g. JSB) ---
	if not has_existing_gate_pass(stock_entry.company):
		gate_pass = frappe.new_doc("Gate Pass")
		gate_pass.document_reference = "Stock Entry"
		gate_pass.reference_number = stock_entry.name
		gate_pass.company = stock_entry.company
		gate_pass.vehicle_number = getattr(stock_entry, "vehicle_no", "")
		gate_pass.driver_name = ""
		gate_pass.driver_contact = ""
		gate_pass.stock_entry = stock_entry.name

		if is_return:
			gate_pass.entry_type = "Gate In"
			gate_pass.return_material_transfer = stock_entry.name
			gate_pass.outbound_material_transfer = stock_entry.return_against
			gate_pass.manual_return_flow = 0
		else:
			# Job work (Thusma ↔ JSB): auto Job Work Out at sender; receiver also Job Work Out.
			gate_pass.set_entry_type()

		gate_pass.populate_gate_pass_items(gate_pass.get_stock_entry_items(stock_entry))
		gate_pass.flags.ignore_mandatory = True
		gate_pass.insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.commit()  # nosemgrep


def cancel_gate_passes_for_stock_entry(stock_entry):
	gate_passes = frappe.get_all(
		"Gate Pass",
		filters={"document_reference": "Stock Entry", "reference_number": stock_entry.name},
		pluck="name",
	)

	for name in gate_passes:
		try:
			gate_pass = frappe.get_doc("Gate Pass", name)
			if gate_pass.docstatus == 1:
				gate_pass.cancel()
			elif gate_pass.docstatus == 0:
				gate_pass.delete()
		except Exception:
			frappe.log_error(title="Gate Pass cancellation failed", message=frappe.get_traceback())
