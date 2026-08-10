# Copyright (c) 2025, Gurudatt Kulkarni and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum
from frappe.utils import cint, cstr, flt, nowdate, nowtime

from gate_entry.constants import (
	INBOUND_REFERENCES,
	INBOUND_STOCK_ENTRY_ENTRY_TYPES,
	INTERCOMPANY_MATERIAL_TRANSFER_DEST_WAREHOUSE_MAP,
	JOB_WORK_IN_DEST_WAREHOUSE_MAP,
	JSB_JOB_WORK_COMPANIES,
	MATERIAL_TRANSFER_STOCK_ENTRY_TYPE,
	OUTBOUND_REFERENCES,
	OUTBOUND_STOCK_ENTRY_ENTRY_TYPES,
	REFERENCE_TOTAL_FIELDS,
	THUSMA_JOB_WORK_COMPANY,
)
from gate_entry.stock_integration import utils as stock_utils

logger = frappe.logger("Gate Pass")

# ------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------
def _normalize_warehouse_name(name: str) -> str:
	"""Normalize a warehouse name for fuzzy comparisons (spaces/dashes)."""
	if not name:
		return ""
	# Collapse whitespace and normalize common dash variants
	normalized = " ".join(cstr(name).replace("–", "-").replace("—", "-").split())
	# Normalize spaces around hyphen for patterns like "ABC  -  XYZ"
	normalized = normalized.replace(" - ", " - ")
	return normalized.strip()


def _resolve_company_warehouse_name(company: str, desired_name: str) -> str | None:
	"""
	Try to resolve the exact Warehouse `name` within a company, even if the
	input has extra spaces / different dash characters.
	"""
	if not company or not desired_name:
		return None

	# Exact match first
	if frappe.db.exists("Warehouse", desired_name):
		return desired_name

	desired_norm = _normalize_warehouse_name(desired_name)
	if not desired_norm:
		return None

	# Search warehouses for this company and compare normalized forms
	available = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0},
		pluck="name",
		limit=500,
	)
	for wh in available:
		if _normalize_warehouse_name(wh) == desired_norm:
			return wh

	return None

# Re-export for backwards compatibility; canonical map is in gate_entry.constants.
INTERCOMPANY_DEST_WAREHOUSE_MAP = INTERCOMPANY_MATERIAL_TRANSFER_DEST_WAREHOUSE_MAP

# Transit warehouse on the sending company side (JSB).
JSB_TRANSIT_WAREHOUSE = "Goods In Transit - JSB-1ZT"


def _normalize_entry_type(entry_type: str | None) -> str:
	return (entry_type or "").strip()


def _is_inbound_stock_entry_entry_type(entry_type: str | None) -> bool:
	return _normalize_entry_type(entry_type) in INBOUND_STOCK_ENTRY_ENTRY_TYPES


def _is_outbound_stock_entry_entry_type(entry_type: str | None) -> bool:
	return _normalize_entry_type(entry_type) in OUTBOUND_STOCK_ENTRY_ENTRY_TYPES


def _is_job_work_participant(company: str | None) -> bool:
	company = _normalize_entry_type(company)
	return company == THUSMA_JOB_WORK_COMPANY or company in JSB_JOB_WORK_COMPANIES


def _is_job_work_company_pair(company_a: str | None, company_b: str | None) -> bool:
	a = _normalize_entry_type(company_a)
	b = _normalize_entry_type(company_b)
	if not a or not b or a == b:
		return False
	return _is_job_work_participant(a) and _is_job_work_participant(b)


def _stock_entry_receiver_company(stock_entry) -> str | None:
	for fn in ("transfer_to_company", "custom_transfer_to_company"):
		val = cstr(getattr(stock_entry, fn, None) or "").strip()
		if val:
			return val
	if cstr(getattr(stock_entry, "party_type", None) or "").strip() == "Company":
		party = cstr(getattr(stock_entry, "party", None) or "").strip()
		if party:
			return party
	return None


def _is_job_work_receiver_entry(
	entry_type: str | None,
	gp_company: str | None,
	ste_company: str | None,
	ste_receiver: str | None = None,
) -> bool:
	"""Job Work Out at receiving company (GP company ≠ STE sender) — acts as inbound."""
	if _normalize_entry_type(entry_type) != "Job Work Out":
		return False
	gp = _normalize_entry_type(gp_company)
	ste = _normalize_entry_type(ste_company)
	receiver = _normalize_entry_type(ste_receiver)
	if not gp or not ste or gp == ste:
		return False
	return _is_job_work_company_pair(gp, ste) or _is_job_work_company_pair(gp, receiver)


def get_stock_entry_quantity_field(
	entry_type: str | None,
	*,
	job_work_receive: bool = False,
) -> str:
	"""Return gate_pass_table qty column used for this Stock Entry entry type."""
	if _is_inbound_stock_entry_entry_type(entry_type):
		return "received_qty"
	if _normalize_entry_type(entry_type) == "Job Work Out" and job_work_receive:
		return "received_qty"
	return "dispatched_qty"


def resolve_job_work_in_dest_warehouse(gp_company: str | None, ste_sender_company: str | None) -> str | None:
	"""Resolve job-work inbound warehouse from (GP company, STE sender company)."""
	gp_company = _normalize_entry_type(gp_company)
	ste_sender_company = _normalize_entry_type(ste_sender_company)
	if not gp_company or not ste_sender_company:
		return None
	return JOB_WORK_IN_DEST_WAREHOUSE_MAP.get((gp_company, ste_sender_company))


class GatePass(Document):
	def is_outbound_reference(self):
		if self.document_reference == "Stock Entry":
			if self._is_job_work_receiver_gate_pass():
				return False
			return _is_outbound_stock_entry_entry_type(self.entry_type)
		return self.document_reference in OUTBOUND_REFERENCES

	def is_inbound_reference(self):
		if self.document_reference == "Stock Entry":
			if _is_inbound_stock_entry_entry_type(self.entry_type):
				return True
			if self._is_job_work_receiver_gate_pass():
				return True
			return False
		return self.document_reference in INBOUND_REFERENCES

	def _is_job_work_receiver_gate_pass(self, stock_entry=None) -> bool:
		if self.document_reference != "Stock Entry" or not self.reference_number:
			return False
		if not stock_entry:
			try:
				stock_entry = frappe.get_cached_doc("Stock Entry", self.reference_number)
			except frappe.DoesNotExistError:
				return False
		return _is_job_work_receiver_entry(
			self.entry_type,
			self.company,
			stock_entry.company,
			_stock_entry_receiver_company(stock_entry),
		)

	def get_stock_entry_quantity_field(self, entry_type=None) -> str:
		return get_stock_entry_quantity_field(
			entry_type or self.entry_type,
			job_work_receive=self._is_job_work_receiver_gate_pass(),
		)

	def get_stock_entry_context(self):
		entry_type = (self.entry_type or "Gate Out").lower()

		if self.document_reference != "Stock Entry" or not self.reference_number:
			return frappe._dict(
				is_stock_entry=False,
				entry_type=entry_type,
			)

		try:
			stock_entry = frappe.get_cached_doc("Stock Entry", self.reference_number)
		except frappe.DoesNotExistError:
			return frappe._dict(
				is_stock_entry=False,
				entry_type=entry_type,
			)

		is_return = stock_utils.is_return_entry(stock_entry)
		manual_return = cint(self.manual_return_flow) == 1 and not is_return

		outbound_reference = None
		if is_return:
			outbound_reference = stock_utils.get_original_outbound_transfer(stock_entry)
		elif manual_return:
			outbound_reference = self.outbound_material_transfer or stock_entry.name

		base_for_items = stock_entry
		if _is_inbound_stock_entry_entry_type(self.entry_type) and outbound_reference:
			try:
				base_for_items = frappe.get_cached_doc("Stock Entry", outbound_reference)
			except frappe.DoesNotExistError:
				base_for_items = stock_entry

		return frappe._dict(
			is_stock_entry=True,
			entry_type=entry_type,
			stock_entry=stock_entry,
			is_return=is_return,
			manual_return=manual_return,
			outbound_reference=outbound_reference,
			base_for_items=base_for_items,
		)

	def before_validate(self):
		"""
		Set derived values before validation
		"""
		self.set_entry_type()
		context = self.get_stock_entry_context()
		self.sync_stock_entry_links(context)
		self.cleanup_discrepancy_fields()

	def before_save(self):
		"""
		Auto-populate fields before saving
		"""
		self.set_entry_type()

		# Auto-populate security guard name with current user
		if not self.security_guard_name:
			user_fullname = frappe.get_value("User", frappe.session.user, "full_name")
			self.security_guard_name = user_fullname or frappe.session.user

		# Auto-populate gate pass date and time
		if not self.gate_pass_date:
			self.gate_pass_date = nowdate()

		if not self.gate_pass_time:
			self.gate_pass_time = nowtime()

		# Auto-populate gate entry date and time
		if not self.gate_entry_date:
			self.gate_entry_date = nowdate()

		if not self.gate_entry_time:
			self.gate_entry_time = nowtime()

		# Clean up driver contact field if it only contains the default country code
		if self.driver_contact:
			cleaned_contact = self.driver_contact.strip().replace(" ", "").replace("-", "")
			if cleaned_contact in ["+91", ""]:
				self.driver_contact = None

	def set_entry_type(self):
		"""
		Derive entry type from reference document
		"""
		if self.document_reference == "Stock Entry":
			self.entry_type = self.derive_entry_type_from_stock_entry()
			return

		if self.is_outbound_reference():
			self.entry_type = "Gate Out"
		else:
			self.entry_type = "Gate In"

	def derive_entry_type_from_stock_entry(self):
		if not self.reference_number:
			return self.entry_type or "Gate Out"

		current = _normalize_entry_type(self.entry_type)
		if current in INBOUND_STOCK_ENTRY_ENTRY_TYPES | OUTBOUND_STOCK_ENTRY_ENTRY_TYPES:
			if current.startswith("Job Work"):
				return current

		try:
			stock_entry = frappe.get_cached_doc("Stock Entry", self.reference_number)
		except frappe.DoesNotExistError:
			return self.entry_type or "Gate Out"

		if cint(self.manual_return_flow):
			return "Gate In"

		if stock_utils.is_material_transfer(stock_entry):
			if stock_utils.is_return_entry(stock_entry):
				return "Gate In"
			gp_company = _normalize_entry_type(self.company)
			ste_company = _normalize_entry_type(stock_entry.company)
			receiver = _stock_entry_receiver_company(stock_entry)
			# Job work (Thusma ↔ JSB): always Job Work Out (sender or receiver).
			if _is_job_work_company_pair(gp_company, ste_company) or _is_job_work_company_pair(
				gp_company, receiver
			):
				return "Job Work Out"
			if gp_company and ste_company and gp_company != ste_company:
				return "Gate In"
			return "Gate Out"

		if stock_utils.is_send_to_subcontractor(stock_entry):
			return "Gate Out"

		return self.entry_type or "Gate Out"

	def sync_stock_entry_links(self, context=None):
		context = context or self.get_stock_entry_context()

		if not context.is_stock_entry:
			self._clear_stock_entry_links()
			return

		stock_entry = context.stock_entry
		self.stock_entry = stock_entry.name

		if context.is_return:
			self.return_material_transfer = stock_entry.name
			self.outbound_material_transfer = context.outbound_reference
			self.manual_return_flow = 0
			return

		if context.manual_return:
			self.return_material_transfer = None
			if context.outbound_reference:
				self.outbound_material_transfer = context.outbound_reference
			else:
				self.outbound_material_transfer = None
			return

		self.return_material_transfer = None
		self.outbound_material_transfer = None
		self.manual_return_flow = 0

	def _clear_stock_entry_links(self):
		self.outbound_material_transfer = None
		self.return_material_transfer = None
		self.stock_entry = None
		self.manual_return_flow = 0

	def cleanup_discrepancy_fields(self):
		if not cint(self.has_discrepancy):
			self.lost_quantity = 0
			self.damaged_quantity = 0
			self.discrepancy_notes = None

	def get_reference_doc(self):
		"""
		Lazy load and cache reference document
		"""
		if not (self.document_reference and self.reference_number):
			return None

		cached_doc = getattr(self, "_reference_doc_cache", None)
		if (
			cached_doc
			and cached_doc.doctype == self.document_reference
			and cached_doc.name == self.reference_number
		):
			return cached_doc

		self._reference_doc_cache = frappe.get_doc(self.document_reference, self.reference_number)

		return self._reference_doc_cache

	def populate_reference_defaults(self, reference_doc=None):
		"""
		Populate company, address, and transport details from reference doc when missing
		"""
		if not (self.document_reference and self.reference_number):
			return

		doc = reference_doc or self.get_reference_doc()
		if not doc:
			return

		# Align company
		if hasattr(doc, "company") and doc.company and not self.company:
			self.company = doc.company

		# Populate address when empty
		if not self.address_display:
			self.address_display = resolve_reference_address(doc, self.document_reference)

		# Populate party details depending on flow
		if self.is_inbound_reference():
			if hasattr(doc, "supplier"):
				self.supplier = doc.supplier
			if hasattr(doc, "supplier_delivery_note") and doc.supplier_delivery_note:
				self.supplier_delivery_note = doc.supplier_delivery_note
			self.clear_compliance_details()
		else:
			self.supplier = None
			self.supplier_delivery_note = None

		# Populate transport fields for outbound documents
		if self.is_outbound_reference():
			transport = extract_transport_details(doc)
			self.set_transport_default("vehicle_number", transport.get("vehicle_number"), doc)
			self.set_transport_default("driver_name", transport.get("driver_name"), doc)
			self.set_transport_default("driver_contact", transport.get("driver_contact"), doc)
			self.apply_compliance_details(extract_compliance_details(doc, self.document_reference))
		else:
			self.clear_compliance_details()

	def set_transport_default(self, fieldname, value, reference_doc):
		"""
		Set transport field if missing
		"""
		if not value or self.get(fieldname):
			return

		self.set(fieldname, value)

	def ensure_outbound_items(self):
		"""
		Populate gate pass items from outbound reference documents
		"""
		if not self.is_outbound_reference():
			return None, None

		reference_doc = self.validate_reference_document()

		if self.document_reference == "Sales Invoice":
			reference_items = get_sales_invoice_items(self.reference_number)
		else:
			reference_items = get_delivery_note_items(self.reference_number)

		if not reference_items:
			frappe.throw(
				_("{0} {1} does not have any items to dispatch").format(
					self.document_reference, self.reference_number
				)
			)

		# Replace the child table with reference items so guards cannot alter quantities
		self.set("gate_pass_table", [])

		for item in reference_items:
			row = self.append("gate_pass_table", {})
			row.item_code = item["item_code"]
			row.item_name = item.get("item_name")
			row.batch_no = item.get("batch_no")
			row.description = item.get("description")
			row.uom = item.get("uom")
			row.stock_uom = item.get("stock_uom")
			row.conversion_factor = flt(item.get("conversion_factor") or 1.0)
			row.ordered_qty = flt(item.get("ordered_qty"))
			row.received_qty = 0
			row.dispatched_qty = flt(item.get("dispatched_qty"))
			row.pending_qty = flt(item.get("pending_qty"))
			row.is_rate_contract = item.get("is_rate_contract") or 0
			row.rate = flt(item.get("rate"))
			row.amount = flt(item.get("amount"))
			row.warehouse = item.get("warehouse")
			row.rejected_warehouse = item.get("rejected_warehouse")
			row.expense_account = item.get("expense_account")
			row.cost_center = item.get("cost_center")
			row.project = item.get("project")
			row.schedule_date = item.get("schedule_date")
			row.bom = item.get("bom")
			row.include_exploded_items = item.get("include_exploded_items") or 0
			row.order_item_name = item.get("order_item_name")

		return reference_doc, reference_items

	def validate_outbound_quantities(self, reference_items):
		"""
		Ensure dispatched quantities match the outbound reference document
		"""
		if not reference_items:
			return

		expected_map = {
			make_reference_item_key(
				item.get("item_code"), item.get("order_item_name"), item.get("warehouse")
			): item
			for item in reference_items
		}

		missing_keys = set(expected_map.keys())

		for row in self.gate_pass_table:
			key = make_reference_item_key(row.item_code, row.order_item_name, row.warehouse)
			if key not in expected_map:
				frappe.throw(
					_("Item {0} is not part of the reference {1}").format(
						row.item_code, self.document_reference
					)
				)

			expected = expected_map[key]
			if abs(flt(row.dispatched_qty) - flt(expected.get("dispatched_qty"))) > 1e-6:
				frappe.throw(
					_("Dispatched quantity for item {0} must match {1}").format(
						row.item_code, flt(expected.get("dispatched_qty"))
					)
				)

			missing_keys.discard(key)

		if missing_keys:
			frappe.throw(
				_("Gate Pass is missing item rows for the reference document: {0}").format(
					", ".join(missing_keys)
				)
			)

	def validate(self):
		"""
		Validate the Gate Pass document
		"""
		context = self.get_stock_entry_context()
		reference_doc = None
		reference_items = None

		if context.is_stock_entry:
			reference_doc, reference_items = self.ensure_stock_entry_items(context)
		elif self.is_outbound_reference():
			reference_doc, reference_items = self.ensure_outbound_items()

		# Validate that at least one item exists
		if not self.gate_pass_table or len(self.gate_pass_table) == 0:
			frappe.throw(_("Please add at least one item to the Gate Pass"))

		# Validate quantities based on document type
		if self.is_outbound_reference():
			for item in self.gate_pass_table:
				if flt(item.dispatched_qty) <= 0:
					frappe.throw(
						_("Dispatched quantity for item {0} must be greater than zero").format(item.item_code)
					)
		else:
			allow_zero = cint(self.manual_return_flow)
			has_positive_receipt = False
			for item in self.gate_pass_table:
				qty = flt(item.received_qty)
				if qty < 0:
					frappe.throw(
						_("Received quantity for item {0} cannot be negative").format(item.item_code)
					)
				if qty > 0:
					has_positive_receipt = True

			if not has_positive_receipt and not allow_zero:
				frappe.throw(_("Please enter a received quantity greater than zero for at least one item."))

		# Validate reference document
		if self.document_reference and self.reference_number:
			if reference_doc:
				doc_for_defaults = reference_doc
			else:
				doc_for_defaults = self.validate_reference_document()
				reference_doc = doc_for_defaults

			self.populate_reference_defaults(doc_for_defaults)
			self.ensure_company_matches_reference(doc_for_defaults)

			if self.is_inbound_reference():
				self.validate_supplier(doc_for_defaults)
			elif self.is_outbound_reference():
				expected_items = reference_items
				if not expected_items:
					expected_items = self.fetch_reference_items()

				self.validate_outbound_quantities(expected_items)
				self.enforce_outbound_compliance(doc_for_defaults)

		if self.document_reference == "Stock Entry" and reference_doc:
			allocation_doc = reference_doc
			if self.is_inbound_reference() and self.outbound_material_transfer:
				allocation_doc = context.base_for_items

			self.validate_stock_entry_allocations(allocation_doc, context)

		self.validate_discrepancy_quantities()
		if self.document_reference == "Purchase Order":
			self.validate_purchase_order_location_and_allocations()

	def validate_purchase_order_location_and_allocations(self):
		"""Require location on submit and block over-receipt vs PO remaining."""
		# Location required when submitting
		if getattr(self, "_action", None) == "submit" or cint(self.docstatus) == 1:
			if not cstr(self.location or "").strip():
				frappe.throw(
					_("Please select Location (warehouse) before submitting Gate In for Purchase Order.")
				)

		location = cstr(self.location or "").strip()
		if location:
			wh_company = frappe.db.get_value("Warehouse", location, "company")
			po_company = frappe.db.get_value("Purchase Order", self.reference_number, "company")
			if wh_company and po_company and wh_company != po_company:
				frappe.throw(
					_("Location warehouse {0} belongs to {1}, but Purchase Order company is {2}.").format(
						location, wh_company, po_company
					)
				)
			if po_company and self.company != po_company:
				frappe.throw(
					_("Gate Pass company must match Purchase Order company {0}.").format(po_company)
				)
			for item in self.gate_pass_table:
				if flt(item.received_qty) > 0:
					item.warehouse = location

		pending_map = {
			row["order_item_name"]: flt(row.get("pending_qty") or 0)
			for row in get_purchase_order_items(self.reference_number, exclude_gate_pass=self.name)
		}
		for item in self.gate_pass_table:
			qty = flt(item.received_qty)
			if qty <= 0 or not item.order_item_name:
				continue
			max_allowed = pending_map.get(item.order_item_name)
			if max_allowed is None:
				continue
			if qty - max_allowed > 1e-6:
				frappe.throw(
					_("Received quantity for item {0} ({1}) exceeds remaining balance ({2}).").format(
						item.item_code, qty, max_allowed
					)
				)

	def fetch_reference_items(self):
		if self.document_reference == "Sales Invoice":
			return get_sales_invoice_items(self.reference_number)
		if self.document_reference == "Delivery Note":
			return get_delivery_note_items(self.reference_number)
		if self.document_reference == "Stock Entry":
			return self.get_stock_entry_items()
		return []

	def ensure_stock_entry_items(self, context=None):
		context = context or self.get_stock_entry_context()

		if not context.is_stock_entry:
			return None, None

		stock_entry = context.stock_entry
		if stock_entry.docstatus != 1:
			frappe.throw(_("Reference document {0} must be submitted").format(self.reference_number))

		base_doc = context.base_for_items or stock_entry
		reference_items = self.get_stock_entry_items(base_doc)
		if not reference_items:
			frappe.throw(_("Stock Entry {0} does not have any items.").format(base_doc.name))

		entry_type = (context.entry_type or (self.entry_type or "Gate Out")).title()
		existing_allocations = self.get_existing_stock_entry_allocations(base_doc.name, entry_type)

		for item in reference_items:
			order_item = item.get("order_item_name")
			if not order_item:
				continue
			allocated = flt(existing_allocations.get(order_item, 0))
			ordered = flt(item.get("ordered_qty") or 0)
			item["pending_qty"] = max(ordered - allocated, 0)

		self.align_gate_pass_items(
			reference_items,
			preserve_quantities=self.is_inbound_reference(),
		)
		self.recalculate_item_amounts()

		return stock_entry, reference_items

	def get_stock_entry_items(self, stock_entry=None):
		stock_entry = stock_entry or self.get_reference_doc()
		is_gate_in = self.is_inbound_reference()

		items = []
		for row in stock_entry.items:
			transfer_qty = abs(flt(getattr(row, "transfer_qty", row.qty)))
			dispatched_qty = 0 if is_gate_in else transfer_qty
			received_qty = transfer_qty if is_gate_in else 0
			pending_qty = transfer_qty

			items.append(
				{
					"item_code": row.item_code,
					"item_name": row.item_name or "",
					"batch_no": get_stock_entry_row_batch_no(row),
					"description": row.description or "",
					"uom": row.uom or row.stock_uom,
					"stock_uom": row.stock_uom,
					"conversion_factor": flt(row.conversion_factor) or 1.0,
					"ordered_qty": transfer_qty,
					"received_qty": received_qty,
					"dispatched_qty": dispatched_qty,
					"pending_qty": pending_qty,
					"is_rate_contract": 0,
					"rate": flt(row.basic_rate) or 0,
					"amount": flt(row.basic_amount) or 0,
					"warehouse": (row.t_warehouse if is_gate_in else row.s_warehouse) or row.s_warehouse or row.t_warehouse,
					"rejected_warehouse": None,
					"expense_account": None,
					"cost_center": row.cost_center,
					"project": row.project,
					"schedule_date": None,
					"bom": None,
					"include_exploded_items": 0,
					"order_item_name": row.name,
				}
			)
		return items

	def populate_gate_pass_items(self, reference_items):
		self.set("gate_pass_table", [])
		for item in reference_items:
			row = self.append("gate_pass_table", {})
			for key, value in item.items():
				row.set(key, value)

	def align_gate_pass_items(self, reference_items, preserve_quantities=False):
		if not reference_items:
			self.set("gate_pass_table", [])
			return

		def get_key(item):
			return item.get("order_item_name") or make_reference_item_key(
				item.get("item_code"), item.get("order_item_name"), item.get("warehouse")
			)

		reference_map = {get_key(item): item for item in reference_items}

		if not self.gate_pass_table:
			self.populate_gate_pass_items(reference_items)
			return

		existing_map = {
			row.order_item_name
			or make_reference_item_key(row.item_code, row.order_item_name, row.warehouse): row
			for row in self.gate_pass_table
		}

		if set(existing_map.keys()) != set(reference_map.keys()):
			self.populate_gate_pass_items(reference_items)
			return

		quantity_field = "received_qty" if preserve_quantities else self.get_stock_entry_quantity_field()

		for key, item in reference_map.items():
			row = existing_map[key]
			preserved_value = flt(row.get(quantity_field) or 0)
			for field, value in item.items():
				if preserve_quantities and field == quantity_field:
					continue
				row.set(field, value)
			if preserve_quantities:
				row.set(quantity_field, preserved_value)

	def recalculate_item_amounts(self):
		is_outbound = self.is_outbound_reference()
		for row in self.gate_pass_table:
			quantity = flt(row.dispatched_qty) if is_outbound else flt(row.received_qty)
			row.amount = flt(row.rate or 0) * quantity

	def validate_discrepancy_quantities(self):
		if not cint(self.has_discrepancy):
			return

		total_qty = sum(flt(item.get("dispatched_qty") or 0) for item in self.gate_pass_table)
		if not total_qty:
			total_qty = sum(flt(item.get("received_qty") or 0) for item in self.gate_pass_table)

		lost_qty = flt(self.lost_quantity or 0)
		damaged_qty = flt(self.damaged_quantity or 0)

		if lost_qty < 0 or damaged_qty < 0:
			frappe.throw(_("Lost/Damaged quantities cannot be negative."))

		if total_qty and (lost_qty + damaged_qty) > total_qty:
			frappe.throw(_("Total lost/damaged quantity cannot exceed movement quantity."))

	def validate_stock_entry_allocations(self, stock_entry, context=None):
		if not stock_entry:
			return

		entry_type = (context.entry_type if context else self.entry_type) or "Gate Out"
		entry_type = entry_type.title()
		target_map = {
			row.name: flt(getattr(row, "transfer_qty", row.qty)) for row in getattr(stock_entry, "items", [])
		}
		if not target_map:
			return

		existing = self.get_existing_stock_entry_allocations(stock_entry.name, entry_type)
		quantity_field = self.get_stock_entry_quantity_field(entry_type)

		for item in self.gate_pass_table:
			order_item = item.order_item_name
			if not order_item:
				continue

			max_qty = target_map.get(order_item)
			if max_qty is None:
				continue

			current = flt(item.get(quantity_field) or 0)
			if current <= 0 and not (
				_is_inbound_stock_entry_entry_type(entry_type) and cint(self.manual_return_flow)
			):
				frappe.throw(_("Quantity for item {0} must be greater than zero.").format(item.item_code))

			allocated = flt(existing.get(order_item, 0))
			if (allocated + current) - max_qty > 1e-6:
				remaining = flt(max_qty - allocated)
				frappe.throw(
					_("Quantity for item {0} exceeds remaining balance ({1}).").format(
						item.item_code, remaining
					)
				)

	def get_existing_stock_entry_allocations(self, stock_entry_name, entry_type):
		"""
		Get existing stock entry allocations with database-level locking to prevent race conditions.

		Uses SELECT FOR UPDATE to lock rows during query, ensuring concurrent transactions
		wait for each other and preventing over-allocation when multiple gate passes are
		validated simultaneously for the same stock entry.
		"""
		column = self.get_stock_entry_quantity_field(entry_type)

		# Use frappe.qb with for_update() to lock rows and prevent race conditions
		gate_pass = DocType("Gate Pass")

		query = (
			frappe.qb.from_(gate_pass)
			.select(gate_pass.name)
			.where(gate_pass.document_reference == "Stock Entry")
			.where(gate_pass.docstatus < 2)
			.where(
				(gate_pass.reference_number == stock_entry_name)
				| (gate_pass.outbound_material_transfer == stock_entry_name)
			)
		)

		# Exclude current document if it exists
		if self.name:
			query = query.where(gate_pass.name != self.name)

		# Lock rows with FOR UPDATE to prevent concurrent reads
		gate_pass_names = query.for_update().run(pluck=True)

		if not gate_pass_names:
			return {}

		# Query child table - parent rows are already locked, ensuring consistency
		gate_pass_table = DocType("Gate Pass Table")
		records = (
			frappe.qb.from_(gate_pass_table)
			.select(gate_pass_table.order_item_name, Sum(gate_pass_table[column]).as_("total"))
			.where(gate_pass_table.parent.isin(gate_pass_names))
			.groupby(gate_pass_table.order_item_name)
		).run(as_dict=True)

		return {row.order_item_name: flt(row.total or 0) for row in records}

	def update_stock_entry_reference(self):
		if self.document_reference != "Stock Entry" or not self.reference_number:
			return

		if self.docstatus != 1:
			return

		if not frappe.db.exists("Stock Entry", self.reference_number):
			return

		frappe.db.set_value("Stock Entry", self.reference_number, "gate_pass", self.name)

	def clear_stock_entry_reference(self):
		"""
		Clear the gate_pass reference from Stock Entry when Gate Pass is cancelled.
		This allows the Gate Pass to be cancelled independently, similar to
		how Purchase Receipt and Subcontracting Receipt work.
		"""
		if self.document_reference != "Stock Entry" or not self.reference_number:
			return

		if not frappe.db.exists("Stock Entry", self.reference_number):
			return

		try:
			current_value = frappe.db.get_value("Stock Entry", self.reference_number, "gate_pass")
			if current_value == self.name:
				# Clear the reference even if Stock Entry is submitted
				# This allows Gate Pass to be cancelled independently
				frappe.db.set_value(
					"Stock Entry", self.reference_number, "gate_pass", None, update_modified=False
				)
		except Exception as e:
			# Log error but don't prevent Gate Pass cancellation
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Error clearing Stock Entry gate pass reference"),
				exception=e,
			)

	def apply_compliance_details(self, details):
		self.e_invoice_status = details.get("e_invoice_status")
		self.e_invoice_reference = details.get("e_invoice_reference")
		self.e_waybill_status = details.get("e_waybill_status")
		self.e_waybill_number = details.get("e_waybill_number")

	def clear_compliance_details(self):
		self.e_invoice_status = None
		self.e_invoice_reference = None
		self.e_waybill_status = None
		self.e_waybill_number = None

	def enforce_outbound_compliance(self, reference_doc):
		# Internal Stock Entry transfers do not require e-invoice / e-waybill.
		if self.document_reference == "Stock Entry":
			return

		# Only enforce compliance if GST Settings doctype is available
		if not frappe.db.exists("DocType", "GST Settings"):
			return

		settings = get_gst_settings()
		threshold = flt(settings.get("e_waybill_threshold") or 0)
		if not threshold:
			return

		total_value = self.get_reference_total(reference_doc)
		if flt(total_value) < threshold:
			return

		missing_requirements = []

		if self.document_reference == "Sales Invoice" and not is_generated_status(self.e_invoice_status):
			missing_requirements.append(_("E-Invoice"))

		require_ewaybill = True
		if self.document_reference == "Delivery Note" and not cint(settings.get("enable_e_waybill_from_dn")):
			require_ewaybill = False
		if require_ewaybill and not is_generated_status(self.e_waybill_status):
			missing_requirements.append(_("E-Way Bill"))

		if not missing_requirements:
			return

		frappe.throw(
			_("Cannot submit Gate Pass because the following compliance documents are missing: {0}").format(
				", ".join(missing_requirements)
			),
			title=_("Compliance Validation Failed"),
		)

	def get_reference_total(self, reference_doc):
		if not reference_doc:
			return 0

		for fieldname in REFERENCE_TOTAL_FIELDS:
			if hasattr(reference_doc, fieldname):
				value = reference_doc.get(fieldname)
				if value:
					return value

		return 0

	def ensure_company_matches_reference(self, reference_doc):
		"""
		Ensure Gate Pass company aligns with reference document.
		For Stock Entry references we always skip the company-match check
		because both same-company and inter-company transfers are valid:
		  - Same company (JSB Gate Out): company will match naturally.
		  - Inter-company (JVE Gate In receives JSB goods): company differs
		    intentionally – enforcing a match would break the cross-company flow.
		"""
		if not reference_doc or not hasattr(reference_doc, "company"):
			return

		reference_company = reference_doc.company
		if not reference_company:
			return

		# For Stock Entry references, skip company validation entirely.
		# Inter-company transfers intentionally cross company boundaries.
		if self.document_reference == "Stock Entry":
			if not self.company:
				self.company = reference_company
			return  # ← do NOT throw for Stock Entry regardless of entry_type

		if not self.company:
			self.company = reference_company
		elif self.company != reference_company:
			frappe.throw(
				_("Company {0} does not match reference document company {1}").format(
					self.company, reference_company
				)
			)

	def validate_reference_document(self):
		"""
		Validate that the reference document is submitted
		"""
		doc = self.get_reference_doc()
		if doc.docstatus != 1:
			frappe.throw(_("Reference document {0} must be submitted").format(self.reference_number))
		return doc

	def validate_supplier(self, reference_doc=None):
		"""
		Validate that supplier matches the reference document supplier
		"""
		if not (self.document_reference and self.reference_number and self.supplier):
			return

		doc = reference_doc or self.get_reference_doc()
		if hasattr(doc, "supplier") and doc.supplier != self.supplier:
			frappe.throw(_("Supplier does not match the reference document"))

	def before_submit(self):
		"""
		Validation before submission
		"""
		# For inbound gate passes (non-outbound), ensure at least one item has positive received_qty
		# This applies even for manual return flows - zero quantities are allowed during draft,
		# but at least one positive quantity is required on submission
		if not self.is_outbound_reference():
			has_positive_receipt = False
			for item in self.gate_pass_table:
				if flt(item.received_qty) > 0:
					has_positive_receipt = True
					break

			if not has_positive_receipt:
				frappe.throw(
					_(
						"Please enter a received quantity greater than zero for at least one item before submitting."
					)
				)

	def on_submit(self):
		"""
		Actions to perform on submission
		"""
		# Check if this is an amended document with linked receipts
		if self.amended_from:
			self.check_receipts_in_amended_document()

		frappe.msgprint(_("Gate Pass submitted successfully"))
		self.update_stock_entry_reference()

		# Auto-create inter-company stock transfers whenever a Gate Pass for a
		# Stock Entry is submitted under a DIFFERENT company from the reference.
		# Works regardless of entry_type (Gate In or Gate Out) so that even
		# auto-created "Gate Out" passes that the user re-assigned to JVE still
		# trigger the Material Issue + Material Receipt creation.
		if self.document_reference == "Stock Entry" and self.reference_number:
			ref_company = frappe.db.get_value("Stock Entry", self.reference_number, "company")
			if ref_company and ref_company != self.company:
				self.create_intercompany_stock_transfers()
			elif cstr(self.location or "").strip() == "":
				# Same-company STE: still record location from first item warehouse if present
				for row in self.gate_pass_table or []:
					if row.warehouse:
						self.db_set("location", row.warehouse, update_modified=False)
						break

		# PO Gate In: auto-create draft Purchase Receipt with location warehouse
		if self.document_reference == "Purchase Order" and not self.purchase_receipt:
			try:
				pr_name = create_purchase_receipt(self.name)
				frappe.msgprint(
					_("Draft Purchase Receipt {0} created. Review and submit to add stock.").format(
						frappe.utils.get_link_to_form("Purchase Receipt", pr_name)
					),
					indicator="green",
				)
			except Exception as e:
				frappe.log_error(
					title=_("Auto Purchase Receipt failed for Gate Pass {0}").format(self.name),
					message=frappe.get_traceback(),
				)
				frappe.msgprint(
					_(
						"Gate Pass submitted but draft Purchase Receipt could not be created:<br>{0}<br>"
						"Use <b>Create Purchase Receipt</b> after fixing the issue."
					).format(frappe.utils.escape_html(cstr(e))),
					indicator="orange",
					title=_("Purchase Receipt not created"),
				)

	def create_intercompany_stock_transfers(self):
		"""
		When a Gate In Gate Pass under Company B (receiving company, e.g. JVE) is submitted,
		and it references a Stock Entry originally created under Company A (e.g. JSB):

		1. Create and submit a Material Issue in Company A to clear stock from
		   the Goods in Transit warehouse.
		2. Create and submit a Material Receipt in Company B to receive stock
		   into the destination warehouse specified in the Gate Pass rows.

		Both entries are created in Draft first so that validations can run,
		then submitted.  Batch numbers are carried across; if the batch does not
		yet exist in the destination company the system will create it.
		"""
		outbound_se = frappe.get_doc("Stock Entry", self.reference_number)
		src_company = outbound_se.company       # e.g. Jayashree Spunbond (JSB)
		dst_company = self.company              # e.g. J Vasanth Exports (JVE)

		# ---- 1. Build item maps from the outbound Stock Entry ----
		outbound_item_map = {row.name: row for row in outbound_se.items}

		# ---- 2. Material Issue in source company (JSB) ----
		# Issues items out of their transit warehouse (t_warehouse of original SE)
		issue_se = frappe.new_doc("Stock Entry")
		issue_se.stock_entry_type = "Material Issue"
		issue_se.company = src_company
		issue_se.posting_date = self.gate_pass_date or nowdate()
		issue_se.posting_time = self.gate_pass_time or nowtime()
		issue_se.set_posting_time = 1
		issue_se.remarks = _("Auto-created by Gate Pass {0} – inter-company transfer out").format(self.name)

		# Tell ERPNext to accept batch_no directly on items instead of requiring
		# Serial and Batch Bundle documents. This is the v15 way.
		if frappe.get_meta("Stock Entry").has_field("use_serial_batch_fields"):
			issue_se.use_serial_batch_fields = 1

		for gp_item in self.gate_pass_table:
			# Use received_qty (Gate In) OR dispatched_qty (Gate Out) OR ordered_qty.
			# Handles auto-created Gate Out passes re-assigned to JVE company.
			received_qty = flt(gp_item.received_qty) or flt(gp_item.dispatched_qty) or flt(gp_item.ordered_qty)
			if received_qty <= 0:
				continue

			ob_row = outbound_item_map.get(gp_item.order_item_name)
			if not ob_row:
				continue

			# Transit warehouse = where JSB's outbound SE put the goods
			transit_wh = ob_row.t_warehouse
			if not transit_wh:
				frappe.throw(
					_("Outbound Stock Entry {0} item {1} has no target warehouse set.").format(
						self.reference_number, gp_item.item_code
					)
				)

			# Resolve batch from all possible sources
			batch_no = self._resolve_batch_no(gp_item, ob_row)

			issue_item = {
				"item_code": gp_item.item_code,
				"item_name": gp_item.item_name,
				"qty": received_qty,
				"uom": gp_item.uom or ob_row.uom,
				"stock_uom": gp_item.stock_uom or ob_row.stock_uom,
				"conversion_factor": flt(gp_item.conversion_factor) or 1.0,
				"s_warehouse": transit_wh,
				"batch_no": batch_no,
				"basic_rate": flt(gp_item.rate) or flt(ob_row.basic_rate),
				"cost_center": ob_row.cost_center,
				"project": ob_row.project,
			}
			issue_se.append("items", issue_item)

		if not issue_se.items:
			frappe.throw(_("No items with received quantity found on Gate Pass {0}").format(self.name))

		issue_se.insert(ignore_permissions=True)
		issue_se.submit()

		# ---- 3. Material Receipt in destination company (JVE) ----
		receipt_se = frappe.new_doc("Stock Entry")
		receipt_se.stock_entry_type = "Material Receipt"
		receipt_se.company = dst_company
		receipt_se.posting_date = self.gate_pass_date or nowdate()
		receipt_se.posting_time = self.gate_pass_time or nowtime()
		receipt_se.set_posting_time = 1
		receipt_se.remarks = _("Auto-created by Gate Pass {0} – inter-company transfer in").format(self.name)

		# Tell ERPNext to accept batch_no directly on items
		if frappe.get_meta("Stock Entry").has_field("use_serial_batch_fields"):
			receipt_se.use_serial_batch_fields = 1

		for gp_item in self.gate_pass_table:
			# Use received_qty (Gate In) OR dispatched_qty (Gate Out) OR ordered_qty.
			# Handles auto-created Gate Out passes re-assigned to JVE company.
			received_qty = flt(gp_item.received_qty) or flt(gp_item.dispatched_qty) or flt(gp_item.ordered_qty)
			if received_qty <= 0:
				continue

			ob_row = outbound_item_map.get(gp_item.order_item_name)

			# Target warehouse for JVE is taken from the Gate Pass row.
			# But if it's from the wrong company (e.g. copied from source SE), ignore it.
			dest_wh = gp_item.warehouse
			if dest_wh:
				wh_company = frappe.db.get_value("Warehouse", dest_wh, "company")
				if wh_company != dst_company:
					dest_wh = None

			# Job work receive: warehouse from (receiving GP company, STE sender company).
			if _normalize_entry_type(self.entry_type) == "Job Work In" or self._is_job_work_receiver_gate_pass(
				outbound_se
			):
				dest_wh = resolve_job_work_in_dest_warehouse(dst_company, src_company)
				if not dest_wh:
					frappe.throw(
						_(
							"No job-work inbound warehouse configured for receiving company {0} "
							"with sender {1}. Update JOB_WORK_DEST_WAREHOUSE_MAP in gate_entry/constants.py."
						).format(dst_company, src_company)
					)

			# Material Transfer Gate In: use configured destination warehouse first
			# (e.g. Raw Materials for VTP — avoid picking the wrong Finished Goods warehouse).
			if (
				not dest_wh
				and outbound_se.stock_entry_type == MATERIAL_TRANSFER_STOCK_ENTRY_TYPE
			):
				dest_wh = INTERCOMPANY_MATERIAL_TRANSFER_DEST_WAREHOUSE_MAP.get(dst_company)

			# Fall back to any Finished Goods warehouse for the receiving company
			if not dest_wh:
				found_wh = frappe.get_all(
					"Warehouse",
					filters={"company": dst_company, "is_group": 0, "name": ["like", "%Finished Goods%"]},
					pluck="name",
					limit=1,
				)
				if found_wh:
					dest_wh = found_wh[0]

			if not dest_wh:
				dest_wh = INTERCOMPANY_MATERIAL_TRANSFER_DEST_WAREHOUSE_MAP.get(dst_company)

			if not dest_wh:
				frappe.throw(
					_(
						"No destination warehouse configured for company {0}. "
						"Please set a warehouse in the Gate Pass item row, or add it to "
						"INTERCOMPANY_MATERIAL_TRANSFER_DEST_WAREHOUSE_MAP in gate_entry/constants.py."
					).format(dst_company)
				)

			# Resolve exact warehouse name for this company (tolerate spacing/dash differences)
			resolved = _resolve_company_warehouse_name(dst_company, dest_wh)
			if resolved:
				dest_wh = resolved

			# Validate the warehouse actually exists in the system
			if not frappe.db.exists("Warehouse", dest_wh):
				# Fetch up to 20 warehouses so the hint is actually useful
				available = frappe.get_all(
					"Warehouse",
					filters={"company": dst_company, "is_group": 0},
					pluck="name",
					limit=20,
				)
				hint = (_(", ").join(available)) if available else _("(none found)")
				frappe.throw(
					_(
						"Warehouse <b>{0}</b> does not exist in this system.<br>"
						"Available warehouses for {1}: {2}<br><br>"
						"Please create the warehouse first or update "
						"INTERCOMPANY_MATERIAL_TRANSFER_DEST_WAREHOUSE_MAP in gate_entry/constants.py."
					).format(dest_wh, dst_company, hint)
				)

			batch_no = self._resolve_batch_no(gp_item, ob_row)

			# Auto-create batch in destination company if needed
			if batch_no:
				self.ensure_batch_exists(batch_no, gp_item.item_code)

			# Build receipt item with batch_no set directly
			receipt_item = {
				"item_code": gp_item.item_code,
				"item_name": gp_item.item_name,
				"qty": received_qty,
				"uom": gp_item.uom or (ob_row.uom if ob_row else None),
				"stock_uom": gp_item.stock_uom or (ob_row.stock_uom if ob_row else None),
				"conversion_factor": flt(gp_item.conversion_factor) or 1.0,
				"t_warehouse": dest_wh,
				"batch_no": batch_no,
				"basic_rate": flt(gp_item.rate) or (flt(ob_row.basic_rate) if ob_row else 0),
			}
			receipt_se.append("items", receipt_item)

		if not receipt_se.items:
			frappe.throw(_("No items to receive for Gate Pass {0}").format(self.name))

		receipt_se.insert(ignore_permissions=True)
		receipt_se.submit()

		# ---- 4. Store references on the Gate Pass ----
		self.db_set("intercompany_issue_entry", issue_se.name, update_modified=False)
		self.db_set("intercompany_receipt_entry", receipt_se.name, update_modified=False)
		# Persist resolved destination warehouse on location (first receipt row)
		first_dest = None
		for row in receipt_se.items:
			if row.t_warehouse:
				first_dest = row.t_warehouse
				break
		if first_dest:
			self.db_set("location", first_dest, update_modified=False)

		frappe.msgprint(
			_(
				"Inter-company stock entries created successfully:<br>"
				"• Material Issue in {0}: <b>{1}</b><br>"
				"• Material Receipt in {2}: <b>{3}</b>"
			).format(src_company, issue_se.name, dst_company, receipt_se.name),
			title=_("Inter-Company Transfer Complete"),
			indicator="green",
		)

	def _apply_batch_to_item(self, item_dict, item_code, warehouse, batch_no, qty, company, transaction_type):
		"""
		Apply batch tracking to a stock entry item dict.
		Detects ERPNext version:
		  - Old style (v13 / use_serial_batch_fields=1): sets batch_no directly
		  - New style (v14+ bundle mode): creates Serial and Batch Bundle doc
		"""
		if not batch_no:
			return  # Item is not batch-tracked or batch not found

		# Detect whether we should use old-style batch_no or new-style bundle.
		# Stock Settings 'use_serial_batch_fields' = 1 means old style is still active.
		use_old_style = True
		try:
			use_sbb = frappe.db.get_single_value("Stock Settings", "use_serial_batch_fields")
			# If use_serial_batch_fields = 1 → old style. If 0 or missing → bundle mode.
			use_old_style = cint(use_sbb) == 1
		except Exception:
			use_old_style = True  # Default to old style if setting doesn't exist (v13)

		if use_old_style:
			# Old ERPNext: set batch_no directly on the item
			item_dict["batch_no"] = batch_no
			return

		# New ERPNext v14+ bundle mode: create a Serial and Batch Bundle
		try:
			self.ensure_batch_exists(batch_no, item_code)
			bundle = frappe.new_doc("Serial and Batch Bundle")
			bundle.item_code = item_code
			bundle.warehouse = warehouse
			bundle.voucher_type = "Stock Entry"
			bundle.type_of_transaction = transaction_type  # "Inward" or "Outward"
			bundle.company = company
			bundle.has_batch_no = 1
			bundle.has_serial_no = 0
			# Outward qty is negative, Inward is positive
			entry_qty = -abs(flt(qty)) if transaction_type == "Outward" else abs(flt(qty))
			bundle.append("entries", {
				"batch_no": batch_no,
				"qty": entry_qty,
			})
			bundle.flags.ignore_permissions = True
			bundle.flags.ignore_mandatory = True
			bundle.insert()
			item_dict["serial_and_batch_bundle"] = bundle.name
		except Exception as e:
			# If bundle creation fails, fall back to direct batch_no as last resort
			frappe.log_error(
				message=frappe.get_traceback(),
				title=f"Serial/Batch Bundle creation failed for {item_code} – falling back to batch_no",
			)
			item_dict["batch_no"] = batch_no

	def _resolve_batch_no(self, gp_item, ob_row=None):
		"""
		Resolve batch_no using 5 fallback levels to cover all ERPNext versions:
		  1. Gate Pass item's own batch_no field
		  2. Outbound SE row's batch_no (direct field)
		  2b. Outbound SE row's serial_and_batch_bundle (ERPNext v14+)
		  3. Direct DB query on Stock Entry Detail batch_no field
		  3b. Stock Entry Detail's serial_and_batch_bundle (ERPNext v14+)
		  4. Stock Ledger Entry for the transit warehouse (most reliable fallback)
		"""
		# Level 1: gate pass item itself
		batch_no = gp_item.batch_no
		if batch_no:
			return batch_no

		# Level 2: matched outbound SE row — direct batch_no field
		if ob_row:
			batch_no = ob_row.batch_no or ob_row.get("batch_no")
			if batch_no:
				return batch_no

			# Level 2b: serial_and_batch_bundle (ERPNext v14+)
			bundle = ob_row.get("serial_and_batch_bundle")
			if bundle:
				batch_no = frappe.db.get_value(
					"Serial and Batch Entry",
					{"parent": bundle},
					"batch_no",
				)
				if batch_no:
					return batch_no

		# Level 3: direct DB on Stock Entry Detail
		if self.reference_number and gp_item.item_code:
			row = frappe.db.get_value(
				"Stock Entry Detail",
				{"parent": self.reference_number, "item_code": gp_item.item_code},
				["batch_no", "serial_and_batch_bundle"],
				as_dict=True,
			)
			if row:
				if row.get("batch_no"):
					return row["batch_no"]
				# Level 3b: bundle in Stock Entry Detail (ERPNext v14+)
				if row.get("serial_and_batch_bundle"):
					batch_no = frappe.db.get_value(
						"Serial and Batch Entry",
						{"parent": row["serial_and_batch_bundle"]},
						"batch_no",
					)
					if batch_no:
						return batch_no

		# Level 4: Stock Ledger Entry for transit warehouse — most reliable
		# Query what batch is actually sitting in the transit warehouse right now
		if gp_item.item_code and self.reference_number:
			try:
				# Get transit warehouse from outbound SE item if available
				transit_wh = ob_row.t_warehouse if ob_row else None
				if transit_wh:
					sle = frappe.db.get_value(
						"Stock Ledger Entry",
						{
							"item_code": gp_item.item_code,
							"warehouse": transit_wh,
							"voucher_no": self.reference_number,
							"is_cancelled": 0,
						},
						["batch_no", "serial_and_batch_bundle"],
						as_dict=True,
					)
					if sle:
						if sle.get("batch_no"):
							return sle["batch_no"]
						if sle.get("serial_and_batch_bundle"):
							batch_no = frappe.db.get_value(
								"Serial and Batch Entry",
								{"parent": sle["serial_and_batch_bundle"]},
								"batch_no",
							)
							if batch_no:
								return batch_no
			except Exception:
				pass

		return None

	def ensure_batch_exists(self, batch_no, item_code):
		"""
		Create a Batch record if it does not already exist.
		This prevents the Material Receipt from failing when the batch
		originated in a different company and has never been seen in
		the destination company's ledger.
		"""
		if not batch_no or not item_code:
			return
		if frappe.db.exists("Batch", batch_no):
			return
		try:
			batch = frappe.new_doc("Batch")
			batch.batch_id = batch_no
			batch.item = item_code
			batch.insert(ignore_permissions=True)
		except Exception:
			# If creation fails (e.g. already created by a parallel process) ignore
			pass

	def before_cancel(self):
		"""
		Clear Stock Entry reference before cancellation to allow independent cancellation.
		This must happen before Frappe's validation runs.
		"""
		# Clear Stock Entry reference first to break the link
		# This allows Gate Pass to be cancelled independently
		self.clear_stock_entry_reference()

		# Check for linked receipts (Purchase Receipt, Subcontracting Receipt)
		# Stock Entry is handled differently - we allow cancellation
		self.check_linked_receipts_before_cancel()

	def on_cancel(self):
		"""
		Actions after successful cancellation.
		Clear references that were needed for cancellation validation.
		"""
		# For manual return flow gate passes without a return Stock Entry,
		# clear outbound_material_transfer and reference_number after successful cancellation.
		# This must happen in on_cancel (not before_cancel) to prevent data loss
		# if cancellation fails due to validation errors.
		if (
			cint(self.manual_return_flow) == 1
			and self.entry_type == "Gate In"
			and not self.return_material_transfer
			and self.document_reference == "Stock Entry"
		):
			# Clear references after successful cancellation
			# Use db_set to directly update the database without triggering validations
			self.db_set("outbound_material_transfer", None, update_modified=False)
			self.db_set("reference_number", None, update_modified=False)

	def check_receipts_in_amended_document(self):
		"""
		Prevent amendment if the original Gate Pass has linked receipts
		"""
		if not self.amended_from:
			return

		# Get the original Gate Pass
		original_doc = frappe.get_doc("Gate Pass", self.amended_from)

		linked_receipts = []

		# Check for Purchase Receipt
		if original_doc.purchase_receipt:
			receipt_status = frappe.db.get_value(
				"Purchase Receipt", original_doc.purchase_receipt, "docstatus"
			)
			if receipt_status == 1:  # Submitted
				linked_receipts.append(
					{
						"doctype": "Purchase Receipt",
						"name": original_doc.purchase_receipt,
						"status": "Submitted",
					}
				)

		# Check for Subcontracting Receipt
		if original_doc.subcontracting_receipt:
			receipt_status = frappe.db.get_value(
				"Subcontracting Receipt", original_doc.subcontracting_receipt, "docstatus"
			)
			if receipt_status == 1:  # Submitted
				linked_receipts.append(
					{
						"doctype": "Subcontracting Receipt",
						"name": original_doc.subcontracting_receipt,
						"status": "Submitted",
					}
				)

		if linked_receipts:
			self.throw_amendment_error(linked_receipts)

	def check_linked_receipts_before_cancel(self):
		"""
		Check if any Purchase Receipt or Subcontracting Receipt is linked
		"""
		linked_receipts = []

		# Check for Purchase Receipt
		if self.purchase_receipt:
			receipt_status = frappe.db.get_value("Purchase Receipt", self.purchase_receipt, "docstatus")
			if receipt_status == 1:  # Submitted
				linked_receipts.append(
					{"doctype": "Purchase Receipt", "name": self.purchase_receipt, "status": "Submitted"}
				)
			elif receipt_status == 0:  # Draft
				linked_receipts.append(
					{"doctype": "Purchase Receipt", "name": self.purchase_receipt, "status": "Draft"}
				)

		# Check for Subcontracting Receipt
		if self.subcontracting_receipt:
			receipt_status = frappe.db.get_value(
				"Subcontracting Receipt", self.subcontracting_receipt, "docstatus"
			)
			if receipt_status == 1:  # Submitted
				linked_receipts.append(
					{
						"doctype": "Subcontracting Receipt",
						"name": self.subcontracting_receipt,
						"status": "Submitted",
					}
				)
			elif receipt_status == 0:  # Draft
				linked_receipts.append(
					{
						"doctype": "Subcontracting Receipt",
						"name": self.subcontracting_receipt,
						"status": "Draft",
					}
				)

		if linked_receipts:
			self.throw_cancellation_error(linked_receipts)

	def throw_cancellation_error(self, linked_receipts):
		"""
		Throw error with list of linked receipts
		"""
		message = _(
			"<b>Cannot cancel this Gate Pass because the following receipt(s) are linked to it:</b><br><br>"
		)

		for receipt in linked_receipts:
			receipt_link = frappe.utils.get_link_to_form(receipt["doctype"], receipt["name"])
			message += _("• {0} - Status: <b>{1}</b><br>").format(receipt_link, receipt["status"])

		message += _("<br><b>Action Required:</b><br>")
		message += _("Please cancel the linked receipt(s) first, then you can cancel this Gate Pass.")

		frappe.throw(message, title=_("Cannot Cancel Gate Pass"))

	def throw_amendment_error(self, linked_receipts):
		"""
		Throw error preventing amendment when receipts exist
		"""
		message = _(
			"<b>Cannot amend this Gate Pass because the following receipt(s) were created from it:</b><br><br>"
		)

		for receipt in linked_receipts:
			receipt_link = frappe.utils.get_link_to_form(receipt["doctype"], receipt["name"])
			message += _("• {0} - Status: <b>{1}</b><br>").format(receipt_link, receipt["status"])

		message += _("<br><b>Action Required:</b><br>")
		message += _("To amend this Gate Pass, please follow these steps:<br>")
		message += _("1. Cancel the linked receipt(s)<br>")
		message += _("2. Cancel this Gate Pass<br>")
		message += _("3. Create a new Gate Pass with the correct details<br>")
		message += _("4. Create a new receipt from the new Gate Pass")

		frappe.throw(message, title=_("Cannot Amend Gate Pass"))


def get_stock_entry_row_batch_no(row) -> str | None:
	"""Resolve batch/roll number from a Stock Entry Detail row."""
	if getattr(row, "batch_no", None):
		return row.batch_no

	bundle = getattr(row, "serial_and_batch_bundle", None)
	if not bundle and hasattr(row, "get"):
		bundle = row.get("serial_and_batch_bundle")

	if bundle:
		return frappe.db.get_value(
			"Serial and Batch Entry",
			{"parent": bundle, "batch_no": ["is", "set"]},
			"batch_no",
			order_by="idx asc",
		)

	return None


@frappe.whitelist()
def get_stock_entry_batches(stock_entry_name, item_code):
	"""
	Return all batch/roll numbers for a given item in a Stock Entry.
	Supports both old-style (batch_no on Stock Entry Detail) and
	ERPNext v14+ (serial_and_batch_bundle → Serial and Batch Entry).

	Used by the Gate Pass info-button popup so the security guard can
	verify which roll/batch numbers are physically being transferred.
	"""
	if not stock_entry_name or not item_code:
		return []

	rows = frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": stock_entry_name, "item_code": item_code},
		fields=["name", "batch_no", "qty", "uom", "stock_uom", "serial_and_batch_bundle"],
		order_by="idx asc",
	)

	batches = []
	for row in rows:
		if row.get("batch_no"):
			# Old-style: batch_no directly on the row
			expiry = frappe.db.get_value("Batch", row.batch_no, "expiry_date")
			batches.append({
				"batch_no": row.batch_no,
				"qty": flt(row.qty),
				"uom": row.uom or row.stock_uom,
				"expiry_date": str(expiry) if expiry else None,
			})
		elif row.get("serial_and_batch_bundle"):
			# ERPNext v14+ style: look inside the bundle
			bundle_entries = frappe.get_all(
				"Serial and Batch Entry",
				filters={"parent": row.serial_and_batch_bundle},
				fields=["batch_no", "qty"],
				order_by="idx asc",
			)
			for be in bundle_entries:
				if be.get("batch_no"):
					expiry = frappe.db.get_value("Batch", be.batch_no, "expiry_date")
					batches.append({
						"batch_no": be.batch_no,
						"qty": abs(flt(be.qty)),
						"uom": row.uom or row.stock_uom,
						"expiry_date": str(expiry) if expiry else None,
					})

	return batches


@frappe.whitelist()
def get_items(document_reference, reference_number, entry_type=None, gp_company=None, exclude_gate_pass=None):
	"""
	Fetch items from the reference document with pending quantities

	Args:
		document_reference: DocType name (Purchase Order or Subcontracting Order)
		reference_number: Document name

	Returns:
		List of items with ordered, received, and pending quantities
	"""
	if not document_reference or not reference_number:
		frappe.throw(_("Document Reference and Reference Number are required"))

	# Check permissions
	if not frappe.has_permission(document_reference, "read"):
		frappe.throw(_("You don't have permission to access {0}").format(document_reference))

	fetchers = {
		"Purchase Order": get_purchase_order_items,
		"Subcontracting Order": get_subcontracting_order_items,
		"Sales Invoice": get_sales_invoice_items,
		"Delivery Note": get_delivery_note_items,
		"Stock Entry": get_stock_entry_items_for_reference,
	}

	try:
		fetcher = fetchers[document_reference]
	except KeyError:
		frappe.throw(_("Unsupported Document Reference: {0}").format(document_reference))

	if document_reference == "Stock Entry":
		return fetcher(reference_number, entry_type=entry_type, gp_company=gp_company)
	if document_reference == "Purchase Order":
		return fetcher(reference_number, exclude_gate_pass=exclude_gate_pass)
	return fetcher(reference_number)


def _stock_entry_items_qty_for_entry_type(
	transfer_qty: float,
	entry_type: str | None,
	is_return: bool,
	*,
	job_work_receive: bool = False,
) -> tuple[float, float]:
	"""Return (received_qty, dispatched_qty) for desk/API item rows."""
	if is_return:
		return flt(transfer_qty), 0.0
	if _is_inbound_stock_entry_entry_type(entry_type) or job_work_receive:
		return flt(transfer_qty), 0.0
	return 0.0, flt(transfer_qty)


def get_purchase_order_items(purchase_order, exclude_gate_pass=None):
	"""
	Get items from Purchase Order with pending quantities and all item details.
	Pending = ordered - PO Item received_qty (from PRs) - other Gate Pass received_qty.
	"""
	# Check if this is a Rate Contract (has_unit_price_items flag)
	po_doc = frappe.get_doc("Purchase Order", purchase_order)
	is_rate_contract = po_doc.get("has_unit_price_items", 0)

	# Fetch items from Purchase Order with all fields
	po_items = frappe.get_all(
		"Purchase Order Item", filters={"parent": purchase_order, "docstatus": 1}, fields=["*"]
	)

	gp_received_by_item = get_gate_pass_received_qty_by_order_item(
		purchase_order, document_reference="Purchase Order", exclude_gate_pass=exclude_gate_pass
	)

	items = []
	for po_item in po_items:
		# PR-updated received qty on PO Item
		pr_received = flt(po_item.received_qty)
		gp_received = flt(gp_received_by_item.get(po_item.name, 0))

		# For Rate Contracts, pending quantity cannot be calculated
		# since ordered quantity is 0
		if is_rate_contract:
			pending_qty = 0  # Not applicable for rate contracts
			ordered_qty = 0
			received_for_row = 0
		else:
			ordered_qty = flt(po_item.qty)
			# PR updates PO.received_qty; open GPs (no PR yet) also reserve pending.
			pending_qty = max(0, ordered_qty - pr_received - gp_received)
			# Prefill received_qty for new Gate In = remaining pending
			received_for_row = pending_qty

		items.append(
			{
				"item_code": po_item.item_code,
				"item_name": po_item.item_name,
				"description": po_item.description or "",
				"uom": po_item.uom,
				"stock_uom": po_item.stock_uom,
				"conversion_factor": flt(po_item.conversion_factor) or 1.0,
				"ordered_qty": ordered_qty,
				"received_qty": received_for_row,
				"dispatched_qty": 0,
				"pending_qty": max(0, pending_qty),
				"is_rate_contract": is_rate_contract,
				"item_group": getattr(po_item, "item_group", None)
				or frappe.db.get_value("Item", po_item.item_code, "item_group"),
				# Pricing details
				"rate": flt(po_item.rate),
				"amount": flt(po_item.amount),
				# Warehouse and location
				"warehouse": po_item.warehouse,
				"rejected_warehouse": None,  # Will be set during receipt
				# Accounting details
				"expense_account": po_item.expense_account,
				"cost_center": po_item.cost_center,
				# Reference details
				"project": po_item.project,
				"schedule_date": po_item.schedule_date,
				# Other details
				"bom": po_item.bom if hasattr(po_item, "bom") else None,
				"order_item_name": po_item.name,  # Store the Purchase Order Item name
			}
		)

	return items


def get_gate_pass_received_qty_by_order_item(
	reference_number, document_reference="Purchase Order", exclude_gate_pass=None
):
	"""Sum Gate Pass Table received_qty by order_item_name for GPs not yet linked to a PR."""
	filters = {
		"reference_number": reference_number,
		"document_reference": document_reference,
		"docstatus": ["<", 2],
	}
	gate_passes = frappe.get_all(
		"Gate Pass", filters=filters, fields=["name", "purchase_receipt"]
	)
	# Only count GPs that have not created a PR yet (PR already updates PO.received_qty).
	open_names = []
	for gp in gate_passes:
		if exclude_gate_pass and gp.name == exclude_gate_pass:
			continue
		if gp.purchase_receipt:
			continue
		open_names.append(gp.name)
	if not open_names:
		return {}

	rows = frappe.get_all(
		"Gate Pass Table",
		filters={"parent": ["in", open_names]},
		fields=["order_item_name", "received_qty"],
	)
	totals = {}
	for row in rows:
		key = row.order_item_name
		if not key:
			continue
		totals[key] = flt(totals.get(key, 0)) + flt(row.received_qty)
	return totals


def get_gate_pass_received_qty(reference_number, item_code, document_reference="Purchase Order"):
	"""
	Calculate total received quantity from existing gate passes for this item

	Args:
		reference_number: Reference document name
		item_code: Item code
		document_reference: DocType name (default: Purchase Order)

	Returns:
		Total received quantity from gate passes
	"""
	gate_passes = frappe.get_all(
		"Gate Pass",
		filters={
			"reference_number": reference_number,
			"document_reference": document_reference,
			"docstatus": ["!=", 2],  # Exclude cancelled
		},
		fields=["name"],
	)

	total_qty = 0
	for gp in gate_passes:
		items = frappe.get_all(
			"Gate Pass Table", filters={"parent": gp.name, "item_code": item_code}, fields=["received_qty"]
		)
		for item in items:
			total_qty += flt(item.received_qty)

	return total_qty


def get_subcontracting_order_items(subcontracting_order):
	"""
	Get items from Subcontracting Order with pending quantities and all item details
	"""
	# Fetch items from Subcontracting Order with all fields
	so_items = frappe.get_all(
		"Subcontracting Order Item", filters={"parent": subcontracting_order, "docstatus": 1}, fields=["*"]
	)

	items = []
	for so_item in so_items:
		# Use received_qty from Subcontracting Order Item (maintained by ERPNext)
		total_received = flt(so_item.received_qty)
		ordered_qty = flt(so_item.qty)
		pending_qty = ordered_qty - total_received

		items.append(
			{
				"item_code": so_item.item_code,
				"item_name": so_item.item_name,
				"description": so_item.description or "",
				"uom": so_item.stock_uom,  # Subcontracting uses stock_uom
				"stock_uom": so_item.stock_uom,
				"conversion_factor": flt(so_item.conversion_factor) or 1.0,
				"ordered_qty": ordered_qty,
				"received_qty": total_received,
				"dispatched_qty": 0,
				"pending_qty": max(0, pending_qty),
				"is_rate_contract": False,  # Subcontracting orders are not rate contracts
				# Pricing details
				"rate": flt(so_item.rate),
				"amount": flt(so_item.amount),
				# Warehouse and location
				"warehouse": so_item.warehouse,
				"rejected_warehouse": None,  # Will be set during receipt
				# Accounting details
				"expense_account": so_item.expense_account,
				"cost_center": so_item.cost_center,
				# Reference details
				"project": so_item.project,
				"schedule_date": so_item.schedule_date,
				# Other details
				"bom": so_item.bom or "",
				"include_exploded_items": so_item.include_exploded_items or 0,
				"order_item_name": so_item.name,  # Store the Subcontracting Order Item name
			}
		)

	return items


def get_sales_invoice_items(sales_invoice):
	"""
	Get items from Sales Invoice for outbound gate processing
	"""
	si_doc = frappe.get_doc("Sales Invoice", sales_invoice)

	items = []
	for si_item in si_doc.get("items", []):
		quantity = flt(si_item.qty)
		items.append(
			{
				"item_code": si_item.item_code,
				"item_name": si_item.item_name or "",
				"description": si_item.description or "",
				"uom": si_item.uom,
				"stock_uom": si_item.stock_uom,
				"conversion_factor": flt(si_item.conversion_factor) or 1.0,
				"ordered_qty": quantity,
				"received_qty": 0,
				"dispatched_qty": quantity,
				"pending_qty": 0,
				"is_rate_contract": 0,
				"warehouse": si_item.warehouse,
				"rejected_warehouse": None,
				"project": si_item.project,
				"schedule_date": getattr(si_item, "delivery_date", None),
				"order_item_name": si_item.name,
			}
		)

	return items


def get_delivery_note_items(delivery_note):
	"""
	Get items from Delivery Note for outbound gate processing
	"""
	dn_doc = frappe.get_doc("Delivery Note", delivery_note)

	items = []
	for dn_item in dn_doc.get("items", []):
		quantity = flt(dn_item.qty)
		items.append(
			{
				"item_code": dn_item.item_code,
				"item_name": dn_item.item_name or "",
				"description": dn_item.description or "",
				"uom": dn_item.uom,
				"stock_uom": dn_item.stock_uom,
				"conversion_factor": flt(dn_item.conversion_factor) or 1.0,
				"ordered_qty": quantity,
				"received_qty": 0,
				"dispatched_qty": quantity,
				"pending_qty": 0,
				"is_rate_contract": 0,
				"warehouse": dn_item.target_warehouse or dn_item.warehouse,
				"rejected_warehouse": None,
				"project": dn_item.project,
				"schedule_date": getattr(dn_item, "schedule_date", None),
				"order_item_name": dn_item.name,
			}
		)

	return items


def get_stock_entry_items_for_reference(stock_entry_name, entry_type=None, gp_company=None):
	stock_entry = frappe.get_doc("Stock Entry", stock_entry_name)
	is_return = cint(getattr(stock_entry, "is_return", 0))

	items = []
	for row in stock_entry.items:
		transfer_qty = abs(flt(getattr(row, "transfer_qty", row.qty)))
		logger.info(f"Stock Entry Item: {row.as_dict()}")

		received_qty, dispatched_qty = _stock_entry_items_qty_for_entry_type(
			transfer_qty,
			entry_type,
			is_return,
			job_work_receive=_is_job_work_receiver_entry(
				entry_type,
				gp_company,
				stock_entry.company,
				_stock_entry_receiver_company(stock_entry),
			),
		)

		item = {
			"item_code": row.item_code,
			"item_name": row.item_name or "",
			"batch_no": get_stock_entry_row_batch_no(row),
			"description": row.description or "",
			"uom": row.uom or row.stock_uom,
			"stock_uom": row.stock_uom,
			"conversion_factor": flt(row.conversion_factor) or 1.0,
			"ordered_qty": transfer_qty,
			"received_qty": received_qty,
			"dispatched_qty": dispatched_qty,
			"pending_qty": transfer_qty,
			"is_rate_contract": 0,
			"rate": flt(row.basic_rate) or 0,
			"amount": flt(row.basic_amount) or 0,
			"warehouse": (row.t_warehouse if is_return else row.s_warehouse) or row.s_warehouse or row.t_warehouse,
			"rejected_warehouse": None,
			"expense_account": None,
			"cost_center": row.cost_center,
			"project": row.project,
			"schedule_date": None,
			"bom": None,
			"include_exploded_items": 0,
			"order_item_name": row.name,
		}

		items.append(item)

	return items


def extract_transport_details(doc):
	"""
	Extract vehicle and driver details from reference documents
	"""
	return {
		"vehicle_number": doc.get("vehicle_number") or doc.get("vehicle_no") or doc.get("vehicle"),
		"driver_name": doc.get("driver_name") or doc.get("driver"),
		"driver_contact": doc.get("driver_contact")
		or doc.get("driver_mobile_no")
		or doc.get("driver_contact_number")
		or doc.get("driver_mobile")
		or doc.get("driver_phone")
		or doc.get("contact_phone"),
	}

@frappe.whitelist()
def get_stock_entry_gate_pass_context(stock_entry_name: str | None = None):
	"""Desk context for Gate Pass job-work / inter-company STE linking (avoids client field-query limits)."""
	stock_entry_name = cstr(stock_entry_name).strip()
	if not stock_entry_name:
		return {}
	if not frappe.db.exists("Stock Entry", stock_entry_name):
		return {}
	if not frappe.has_permission("Stock Entry", "read", stock_entry_name):
		frappe.throw(_("Not permitted to read Stock Entry {0}").format(stock_entry_name))

	doc = frappe.get_doc("Stock Entry", stock_entry_name)
	return {
		"stock_entry_type": doc.stock_entry_type,
		"company": doc.company,
		"receiver_company": _stock_entry_receiver_company(doc),
		"party_type": doc.get("party_type"),
		"party": doc.get("party"),
	}


@frappe.whitelist()
def get_origin_vehicle_details(reference_number):
	"""
	For an inter-company Gate In against a Stock Entry,
	find the original Gate Out and return its vehicle and driver details.
	"""
	gate_pass = frappe.db.get_value(
		"Gate Pass", 
		{
			"reference_number": reference_number,
			"entry_type": "Gate Out"
		}, 
		["vehicle_number", "driver_name", "driver_contact"], 
		as_dict=True
	)
	
	if not gate_pass:
		gate_pass = frappe.db.get_value(
			"Gate Pass", 
			{
				"outbound_material_transfer": reference_number,
				"entry_type": "Gate Out"
			}, 
			["vehicle_number", "driver_name", "driver_contact"], 
			as_dict=True
		)

	return gate_pass or {}


def extract_compliance_details(doc, document_reference):
	"""
	Extract e-invoice and e-waybill information from outbound reference documents
	"""
	details = {
		"e_invoice_status": None,
		"e_invoice_reference": None,
		"e_waybill_status": None,
		"e_waybill_number": None,
	}
	if document_reference not in OUTBOUND_REFERENCES or not doc:
		return details

	irn = getattr(doc, "irn", None)
	irn_cancelled = getattr(doc, "irn_cancelled", None)
	e_invoice_status = getattr(doc, "e_invoice_status", None)

	if document_reference == "Sales Invoice":
		if irn:
			status = "Cancelled" if irn_cancelled else "Generated"
		else:
			status = e_invoice_status or "Not Generated"
	else:
		status = None

	e_waybill_number = getattr(doc, "ewaybill", None) or getattr(doc, "e_waybill_number", None)
	e_waybill_status = getattr(doc, "e_waybill_status", None)

	if e_waybill_number and not e_waybill_status:
		e_waybill_status = "Generated"
	elif not e_waybill_number and not e_waybill_status:
		e_waybill_status = "Not Generated"

	details["e_invoice_status"] = status
	details["e_invoice_reference"] = irn
	details["e_waybill_status"] = e_waybill_status
	details["e_waybill_number"] = e_waybill_number

	return details


def get_gst_settings():
	"""
	Get GST Settings document if India Compliance app is installed.
	Returns empty dict if GST Settings doctype is not available or if there are schema issues.
	"""
	# Check if GST Settings doctype exists (India Compliance app might not be installed)
	if not frappe.db.exists("DocType", "GST Settings"):
		return frappe._dict()

	try:
		return frappe.get_cached_doc("GST Settings")
	except (frappe.DoesNotExistError, Exception):
		# Handle cases where:
		# - GST Settings document doesn't exist
		# - Database schema issues (e.g., missing columns in child tables)
		# - India Compliance app is partially installed or migrations not run
		return frappe._dict()


def is_generated_status(status):
	if not status:
		return False

	status_value = cstr(status).strip().lower()
	return status_value in {"manually generated", "generated", "valid", "active"}


def resolve_reference_address(doc, document_reference):
	"""
	Resolve address display for various reference document types
	"""
	if not doc:
		return ""

	if document_reference in INBOUND_REFERENCES:
		return doc.get("address_display") or ""

	if document_reference == "Sales Invoice":
		return (
			doc.get("shipping_address_display")
			or doc.get("address_display")
			or doc.get("customer_address")
			or ""
		)

	if document_reference == "Delivery Note":
		return doc.get("shipping_address_display") or doc.get("address_display") or ""

	return ""


def make_reference_item_key(item_code, order_item_name=None, warehouse=None):
	"""
	Build a stable key for mapping reference items and gate pass rows
	"""
	if order_item_name:
		return order_item_name
	return f"{item_code or ''}::{warehouse or ''}"


@frappe.whitelist()
def get_address(document_reference, reference_number):
	"""
	Fetch address display from the reference document
	"""
	if not document_reference or not reference_number:
		return ""

	address = ""

	if document_reference == "Purchase Order":
		po = frappe.get_value("Purchase Order", reference_number, "address_display")
		address = po or ""
	elif document_reference == "Subcontracting Order":
		so = frappe.get_value("Subcontracting Order", reference_number, "address_display")
		address = so or ""
	elif document_reference == "Sales Invoice":
		address_fields = frappe.db.get_value(
			"Sales Invoice",
			reference_number,
			["shipping_address_display", "address_display", "customer_address"],
			as_dict=True,
		)
		if address_fields:
			address = (
				address_fields.get("shipping_address_display")
				or address_fields.get("address_display")
				or address_fields.get("customer_address")
				or ""
			)
	elif document_reference == "Delivery Note":
		address_fields = frappe.db.get_value(
			"Delivery Note",
			reference_number,
			["shipping_address_display", "address_display"],
			as_dict=True,
		)
		if address_fields:
			address = (
				address_fields.get("shipping_address_display") or address_fields.get("address_display") or ""
			)

	return address


@frappe.whitelist()
def get_reference_details(document_reference, reference_number):
	"""
	Fetch header-level details from the reference document to prefill gate pass fields
	"""
	if not document_reference or not reference_number:
		frappe.throw(_("Document Reference and Reference Number are required"))

	if not frappe.has_permission(document_reference, "read"):
		frappe.throw(_("You don't have permission to access {0}").format(document_reference))

	doc = frappe.get_doc(document_reference, reference_number)
	transport = extract_transport_details(doc)

	details = {
		"company": getattr(doc, "company", None),
		"address_display": resolve_reference_address(doc, document_reference),
		"vehicle_number": transport.get("vehicle_number"),
		"driver_name": transport.get("driver_name"),
		"driver_contact": transport.get("driver_contact"),
		"posting_date": getattr(doc, "posting_date", None),
		"posting_time": getattr(doc, "posting_time", None),
		"document_date": getattr(doc, "transaction_date", None)
		if hasattr(doc, "transaction_date")
		else getattr(doc, "posting_date", None),
	}

	compliance = extract_compliance_details(doc, document_reference)
	details.update(
		{
			"e_invoice_status": compliance.get("e_invoice_status"),
			"e_invoice_reference": compliance.get("e_invoice_reference"),
			"e_waybill_status": compliance.get("e_waybill_status"),
			"e_waybill_number": compliance.get("e_waybill_number"),
		}
	)

	if document_reference in INBOUND_REFERENCES:
		details.update(
			{
				"party_type": "Supplier",
				"party": getattr(doc, "supplier", None),
				"party_name": getattr(doc, "supplier_name", None),
				"supplier": getattr(doc, "supplier", None),
				"supplier_delivery_note": getattr(doc, "supplier_delivery_note", None),
			}
		)
	elif document_reference in OUTBOUND_REFERENCES:
		details.update(
			{
				"party_type": "Customer",
				"party": getattr(doc, "customer", None),
				"party_name": getattr(doc, "customer_name", None),
				"customer": getattr(doc, "customer", None),
			}
		)

	return details


@frappe.whitelist()
def get_outbound_compliance_status(document_reference, reference_number, gate_pass=None):
	"""
	Return compliance information for outbound gate passes.

	This is a lightweight helper that allows the frontend to display status banners.
	The detailed validation logic is implemented during document validation/submission.
	"""
	if document_reference not in OUTBOUND_REFERENCES:
		return None

	# Placeholder response; detailed compliance enforcement is handled during validation.
	# Future enhancements should extend this method to surface specific warnings/errors.
	return {
		"level": "info",
		"title": _("Compliance checks pending"),
		"messages": [
			_("Compliance validation will run during Gate Pass submission."),
			_("Ensure e-invoice and e-way bill are generated before proceeding."),
		],
	}


@frappe.whitelist()
def create_purchase_receipt(gate_pass_name):
	"""
	Create Purchase Receipt from Gate Pass
	Maps all fields from Purchase Order Item and uses received quantities from Gate Pass

	Args:
		gate_pass_name: Name of the Gate Pass

	Returns:
		Name of the created Purchase Receipt
	"""
	# Check permissions
	if not frappe.has_permission("Purchase Receipt", "create"):
		frappe.throw(_("You don't have permission to create Purchase Receipt"))

	# Get Gate Pass
	gate_pass = frappe.get_doc("Gate Pass", gate_pass_name)

	# Validate Gate Pass
	if gate_pass.docstatus != 1:
		frappe.throw(_("Gate Pass must be submitted before creating Purchase Receipt"))

	if gate_pass.purchase_receipt:
		frappe.throw(_("Purchase Receipt has already been created for this Gate Pass"))

	if gate_pass.document_reference != "Purchase Order":
		frappe.throw(_("This Gate Pass is not for a Purchase Order"))

	# Get Purchase Order document for header-level fields
	purchase_order = frappe.get_doc("Purchase Order", gate_pass.reference_number)
	supplier_has_gstin = _purchase_supplier_has_gstin(purchase_order, gate_pass.supplier)

	# Create Purchase Receipt with header mapping from Purchase Order
	pr = frappe.new_doc("Purchase Receipt")
	pr.supplier = gate_pass.supplier
	pr.company = purchase_order.company
	pr.gate_pass = gate_pass_name
	if gate_pass.get("supplier_delivery_note"):
		pr.supplier_delivery_note = gate_pass.supplier_delivery_note

	# Map additional header fields from Purchase Order
	pr.supplier_warehouse = purchase_order.supplier_warehouse
	pr.currency = purchase_order.currency
	pr.conversion_rate = purchase_order.conversion_rate
	pr.buying_price_list = purchase_order.buying_price_list
	pr.price_list_currency = purchase_order.price_list_currency
	pr.plc_conversion_rate = purchase_order.plc_conversion_rate
	pr.ignore_pricing_rule = purchase_order.ignore_pricing_rule
	location = cstr(gate_pass.get("location") or "").strip()
	pr.set_warehouse = location or purchase_order.set_warehouse
	pr.supplier_address = purchase_order.supplier_address
	pr.address_display = purchase_order.address_display
	pr.contact_person = purchase_order.contact_person
	pr.contact_display = purchase_order.contact_display
	pr.contact_mobile = purchase_order.contact_mobile
	pr.contact_email = purchase_order.contact_email
	pr.shipping_address = purchase_order.shipping_address
	pr.shipping_address_display = purchase_order.shipping_address_display

	# Copy GST / billing header fields when present on PO
	for fieldname in (
		"billing_address",
		"billing_address_display",
		"supplier_gstin",
		"company_gstin",
		"place_of_supply",
		"gst_category",
		"is_reverse_charge",
	):
		if pr.meta.has_field(fieldname) and purchase_order.meta.has_field(fieldname):
			pr.set(fieldname, purchase_order.get(fieldname))

	# set the vehicle number and driver name from gate pass
	pr.vehicle_no = gate_pass.vehicle_number
	pr.driver_name = gate_pass.driver_name

	# Add items - fetch complete details from Purchase Order Item and override quantities from Gate Pass
	for gate_pass_item in gate_pass.gate_pass_table:
		received_qty = flt(gate_pass_item.received_qty)
		if received_qty <= 0:
			continue

		# Get the original Purchase Order Item
		po_item = frappe.get_doc("Purchase Order Item", gate_pass_item.order_item_name)

		# Calculate quantities based on received quantity from Gate Pass
		conversion_factor = flt(po_item.conversion_factor) or 1.0
		received_stock_qty = received_qty * conversion_factor

		# Build Purchase Receipt Item with all fields from PO Item that exist in PR Item
		pr_item = {
			# Basic item details from PO
			"item_code": po_item.item_code,
			"item_name": po_item.item_name,
			"description": po_item.description,
			"item_group": po_item.item_group,
			"brand": po_item.brand,
			"image": po_item.image,
			# UOM and conversion
			"uom": po_item.uom,
			"stock_uom": po_item.stock_uom,
			"conversion_factor": conversion_factor,
			# Quantities - from Gate Pass
			"qty": received_qty,
			"received_qty": received_qty,
			"stock_qty": received_stock_qty,
			"received_stock_qty": received_stock_qty,
			# Pricing from PO (base values will be calculated by set_missing_values)
			"rate": flt(po_item.rate),
			"price_list_rate": flt(po_item.price_list_rate),
			"base_rate": flt(po_item.base_rate),
			"base_price_list_rate": flt(po_item.base_price_list_rate),
			"discount_percentage": flt(po_item.discount_percentage),
			"discount_amount": flt(po_item.discount_amount),
			"margin_type": po_item.margin_type,
			"margin_rate_or_amount": flt(po_item.margin_rate_or_amount),
			# Warehouse - prefer Gate Pass location / row, fallback to PO
			"warehouse": location or gate_pass_item.warehouse or po_item.warehouse,
			"from_warehouse": po_item.from_warehouse if po_item.get("from_warehouse") else None,
			# Accounting from PO
			"expense_account": po_item.expense_account,
			"cost_center": po_item.cost_center,
			# Reference fields from PO
			"project": po_item.project if po_item.get("project") else None,
			"schedule_date": po_item.schedule_date if po_item.get("schedule_date") else None,
			# Material Request references
			"material_request": po_item.material_request if po_item.get("material_request") else None,
			"material_request_item": po_item.material_request_item
			if po_item.get("material_request_item")
			else None,
			# Sales Order references (for drop-ship scenarios)
			"sales_order": po_item.sales_order if po_item.get("sales_order") else None,
			"sales_order_item": po_item.sales_order_item if po_item.get("sales_order_item") else None,
			# Manufacturing references
			"bom": po_item.bom if po_item.get("bom") else None,
			"wip_composite_asset": po_item.wip_composite_asset
			if po_item.get("wip_composite_asset")
			else None,
			# Manufacturer details
			"manufacturer": po_item.manufacturer if po_item.get("manufacturer") else None,
			"manufacturer_part_no": po_item.manufacturer_part_no
			if po_item.get("manufacturer_part_no")
			else None,
			"supplier_part_no": po_item.supplier_part_no if po_item.get("supplier_part_no") else None,
			# Asset fields
			"is_fixed_asset": po_item.is_fixed_asset if po_item.get("is_fixed_asset") else 0,
			"asset_location": po_item.asset_location if po_item.get("asset_location") else None,
			"asset_category": po_item.asset_category if po_item.get("asset_category") else None,
			# Other fields
			"product_bundle": po_item.product_bundle if po_item.get("product_bundle") else None,
			"is_free_item": po_item.is_free_item if po_item.get("is_free_item") else 0,
			# Order linking - Critical for PO-PR linkage
			"purchase_order": gate_pass.reference_number,
			"purchase_order_item": gate_pass_item.order_item_name,
		}

		# Tax / GST — do not charge GST when supplier has no GSTIN (India Compliance)
		if supplier_has_gstin:
			pr_item["item_tax_template"] = po_item.item_tax_template if po_item.get("item_tax_template") else None
			pr_item["item_tax_rate"] = po_item.item_tax_rate if po_item.get("item_tax_rate") else None
			pr_item["gst_treatment"] = po_item.gst_treatment if po_item.get("gst_treatment") else None
		else:
			pr_item["item_tax_template"] = None
			pr_item["item_tax_rate"] = None
			if frappe.get_meta("Purchase Receipt Item").has_field("gst_treatment"):
				pr_item["gst_treatment"] = "Nil Rated"

		# Add rejected_warehouse only if specified in Gate Pass
		if gate_pass_item.get("rejected_warehouse"):
			pr_item["rejected_warehouse"] = gate_pass_item.rejected_warehouse

		# Add apply_tds if present in PO
		if po_item.get("apply_tds"):
			pr_item["apply_tds"] = po_item.apply_tds

		pr.append("items", pr_item)

	if not pr.items:
		frappe.throw(_("No items with received quantity found on Gate Pass {0}").format(gate_pass_name))

	# Set missing values and calculate totals (mimics ERPNext's set_missing_values)
	pr.run_method("set_missing_values")

	if not supplier_has_gstin:
		_strip_gst_from_purchase_receipt(pr)

	try:
		pr.run_method("calculate_taxes_and_totals")
	except Exception:
		pass

	pr.flags.ignore_permissions = True
	pr.insert()

	# Update Gate Pass with receipt reference (works even if GP is submitted)
	frappe.db.set_value("Gate Pass", gate_pass_name, "purchase_receipt", pr.name, update_modified=False)

	return pr.name


def _purchase_supplier_has_gstin(purchase_order, supplier=None) -> bool:
	"""True if PO/supplier has a GSTIN (registered for GST charging)."""
	gstin = cstr(purchase_order.get("supplier_gstin") or "").strip()
	if gstin:
		return True
	supplier = supplier or purchase_order.get("supplier")
	if not supplier:
		return False
	if frappe.get_meta("Supplier").has_field("gstin"):
		return bool(cstr(frappe.db.get_value("Supplier", supplier, "gstin") or "").strip())
	return False


def _strip_gst_from_purchase_receipt(pr):
	"""Remove GST tax rows/templates so India Compliance allows PR without supplier GSTIN."""
	if pr.meta.has_field("taxes_and_charges"):
		pr.taxes_and_charges = None
	pr.set("taxes", [])

	item_meta = frappe.get_meta("Purchase Receipt Item")
	for item in pr.items:
		if item_meta.has_field("item_tax_template"):
			item.item_tax_template = None
		if item_meta.has_field("item_tax_rate"):
			item.item_tax_rate = None
		if item_meta.has_field("gst_treatment"):
			# Unregistered supplier: do not charge GST on items
			item.gst_treatment = "Nil Rated"


@frappe.whitelist()
def get_warehouses_for_company(company):
	"""Non-group warehouses for the given company (PO Gate In location picker)."""
	company = cstr(company).strip()
	if not company:
		return []
	return frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0},
		fields=["name", "warehouse_name", "company"],
		order_by="name asc",
		limit_page_length=500,
	)


def create_gate_pass_from_purchase_order(purchase_order_name: str, enqueued_by: str | None = None):
	"""Auto-create draft Gate In for a submitted Purchase Order."""
	po = frappe.get_doc("Purchase Order", purchase_order_name)
	if po.docstatus != 1:
		return

	existing = frappe.get_all(
		"Gate Pass",
		filters={
			"document_reference": "Purchase Order",
			"reference_number": po.name,
			"company": po.company,
			"docstatus": 0,
		},
		pluck="name",
		limit=1,
	)
	if existing:
		return

	items = get_purchase_order_items(po.name)
	# Skip if nothing pending
	if not any(flt(i.get("pending_qty") or 0) > 0 or flt(i.get("received_qty") or 0) > 0 for i in items):
		# Rate contracts may have zero ordered — still create empty shell? Skip if no items.
		if not items:
			return

	gate_pass = frappe.new_doc("Gate Pass")
	gate_pass.document_reference = "Purchase Order"
	gate_pass.reference_number = po.name
	gate_pass.company = po.company
	gate_pass.entry_type = "Gate In"
	gate_pass.supplier = po.supplier
	gate_pass.address_display = resolve_reference_address(po, "Purchase Order")
	gate_pass.flags.ignore_mandatory = True

	for item in items:
		# Only add lines with remaining qty (or rate-contract lines)
		if not cint(item.get("is_rate_contract")) and flt(item.get("received_qty") or 0) <= 0:
			continue
		row = gate_pass.append("gate_pass_table", {})
		for key, value in item.items():
			if hasattr(row, key) or key in (
				"item_code",
				"item_name",
				"description",
				"uom",
				"stock_uom",
				"conversion_factor",
				"ordered_qty",
				"received_qty",
				"dispatched_qty",
				"pending_qty",
				"rate",
				"amount",
				"warehouse",
				"order_item_name",
				"expense_account",
				"cost_center",
				"project",
				"schedule_date",
				"is_rate_contract",
				"item_group",
			):
				row.set(key, value)

	if not gate_pass.gate_pass_table and not any(cint(i.get("is_rate_contract")) for i in items):
		return

	gate_pass.insert(ignore_permissions=True, ignore_mandatory=True)
	frappe.db.commit()  # nosemgrep


def on_purchase_order_submit(doc, method):
	"""Auto-create draft Gate In when Purchase Order is submitted."""
	if doc.doctype != "Purchase Order" or doc.docstatus != 1:
		return
	frappe.enqueue(
		create_gate_pass_from_purchase_order,
		purchase_order_name=doc.name,
		enqueued_by=frappe.session.user,
		queue="long",
		now=frappe.flags.in_test,
	)


@frappe.whitelist()
def create_subcontracting_receipt(gate_pass_name):
	"""
	Create Subcontracting Receipt from Gate Pass
	Uses proper field mapping between Subcontracting Order and Subcontracting Receipt
	following ERPNext's standard mapper pattern

	Args:
		gate_pass_name: Name of the Gate Pass

	Returns:
		Name of the created Subcontracting Receipt
	"""
	# Check permissions
	if not frappe.has_permission("Subcontracting Receipt", "create"):
		frappe.throw(_("You don't have permission to create Subcontracting Receipt"))

	# Get Gate Pass
	gate_pass = frappe.get_doc("Gate Pass", gate_pass_name)

	# Validate Gate Pass
	if gate_pass.docstatus != 1:
		frappe.throw(_("Gate Pass must be submitted before creating Subcontracting Receipt"))

	if gate_pass.subcontracting_receipt:
		frappe.throw(_("Subcontracting Receipt has already been created for this Gate Pass"))

	if gate_pass.document_reference != "Subcontracting Order":
		frappe.throw(_("This Gate Pass is not for a Subcontracting Order"))

	# Get Subcontracting Order document for header-level fields
	subcontracting_order = frappe.get_doc("Subcontracting Order", gate_pass.reference_number)

	# Create Subcontracting Receipt with proper header field mapping
	sr = frappe.new_doc("Subcontracting Receipt")

	# Primary fields
	sr.supplier = subcontracting_order.supplier
	sr.company = subcontracting_order.company
	sr.vehicle_no = gate_pass.vehicle_number

	# Reference to Gate Pass
	sr.gate_pass = gate_pass_name
	if gate_pass.get("supplier_delivery_note"):
		sr.supplier_delivery_note = gate_pass.supplier_delivery_note

	# Map header fields from Subcontracting Order to Subcontracting Receipt
	# Following ERPNext's standard field mapping (see make_subcontracting_receipt in subcontracting_order.py)
	header_field_map = {
		# Warehouse fields
		"supplier_warehouse": "supplier_warehouse",
		"set_warehouse": "set_warehouse",
		# Address and contact fields
		"supplier_address": "supplier_address",
		"address_display": "address_display",
		"contact_person": "contact_person",
		"contact_display": "contact_display",
		"contact_mobile": "contact_mobile",
		"contact_email": "contact_email",
		"shipping_address": "shipping_address",
		"shipping_address_display": "shipping_address_display",
		"billing_address": "billing_address",
		"billing_address_display": "billing_address_display",
		# Project and cost center
		"project": "project",
		"cost_center": "cost_center",
		# Print and display settings
		"letter_head": "letter_head",
		"select_print_heading": "select_print_heading",
		# Additional costs
		"distribute_additional_costs_based_on": "distribute_additional_costs_based_on",
		# Critical: Purchase Order reference (needed for proper linking)
		"purchase_order": "purchase_order",
	}

	for source_field, target_field in header_field_map.items():
		if subcontracting_order.get(source_field):
			sr.set(target_field, subcontracting_order.get(source_field))

	# Add items - Map fields from Subcontracting Order Item to Subcontracting Receipt Item
	# Following ERPNext's field mapping standard
	for gate_pass_item in gate_pass.gate_pass_table:
		# Get the original Subcontracting Order Item
		so_item = frappe.get_doc("Subcontracting Order Item", gate_pass_item.order_item_name)

		# Calculate quantities based on received quantity from Gate Pass
		received_qty = flt(gate_pass_item.received_qty)
		conversion_factor = flt(so_item.conversion_factor) or 1.0

		# Build Subcontracting Receipt Item with proper field mapping
		# Only map fields that exist in both Subcontracting Order Item and Subcontracting Receipt Item
		sr_item = {
			# Basic item details
			"item_code": so_item.item_code,
			"item_name": so_item.item_name,
			"description": so_item.description,
			# Brand and image (exist in SR Item)
			"brand": so_item.brand if so_item.get("brand") else None,
			"image": so_item.image if so_item.get("image") else None,
			# UOM and conversion
			"stock_uom": so_item.stock_uom,
			"conversion_factor": conversion_factor,
			# Quantities - from Gate Pass
			"qty": received_qty,
			"received_qty": received_qty,
			# Pricing from Subcontracting Order Item
			"rate": flt(so_item.rate),
			# Cost breakdown fields (specific to subcontracting)
			"rm_cost_per_qty": flt(so_item.rm_cost_per_qty) if so_item.get("rm_cost_per_qty") else 0,
			"service_cost_per_qty": flt(so_item.service_cost_per_qty)
			if so_item.get("service_cost_per_qty")
			else 0,
			"additional_cost_per_qty": flt(so_item.additional_cost_per_qty)
			if so_item.get("additional_cost_per_qty")
			else 0,
			# Warehouse - prefer from Gate Pass, fallback to Subcontracting Order
			"warehouse": gate_pass_item.warehouse or so_item.warehouse,
			# Accounting fields
			"expense_account": so_item.expense_account if so_item.get("expense_account") else None,
			"cost_center": so_item.cost_center if so_item.get("cost_center") else None,
			# Reference fields
			"project": so_item.project if so_item.get("project") else None,
			"schedule_date": so_item.schedule_date if so_item.get("schedule_date") else None,
			# Subcontracting specific fields - Critical for subcontracting workflow
			"bom": so_item.bom,
			"include_exploded_items": so_item.include_exploded_items
			if so_item.get("include_exploded_items")
			else 0,
			# Manufacturer details
			"manufacturer": so_item.manufacturer if so_item.get("manufacturer") else None,
			"manufacturer_part_no": so_item.manufacturer_part_no
			if so_item.get("manufacturer_part_no")
			else None,
			# Other fields
			"page_break": so_item.page_break if so_item.get("page_break") else 0,
			"job_card": so_item.job_card if so_item.get("job_card") else None,
			# Critical linking fields - Required for proper SO-SR linkage and status updates
			"subcontracting_order": gate_pass.reference_number,
			"subcontracting_order_item": gate_pass_item.order_item_name,
			# Purchase Order references - Critical for proper linking to PO
			"purchase_order": subcontracting_order.purchase_order,
			"purchase_order_item": so_item.purchase_order_item
			if so_item.get("purchase_order_item")
			else None,
		}

		# Add rejected_warehouse only if specified in Gate Pass
		if gate_pass_item.get("rejected_warehouse"):
			sr_item["rejected_warehouse"] = gate_pass_item.rejected_warehouse

		sr.append("items", sr_item)

	# Copy additional costs table if present
	if subcontracting_order.get("additional_costs"):
		for cost in subcontracting_order.additional_costs:
			sr.append(
				"additional_costs",
				{
					"expense_account": cost.expense_account,
					"description": cost.description,
					"amount": cost.amount,
					"base_amount": cost.base_amount if cost.get("base_amount") else None,
				},
			)

	# Set missing values and calculate totals
	# This will populate supplied_items, calculate rates, and perform all necessary calculations
	sr.run_method("set_missing_values")

	# Insert the Subcontracting Receipt
	sr.insert()

	# Update Gate Pass with receipt reference
	gate_pass.subcontracting_receipt = sr.name
	gate_pass.save(ignore_permissions=True)

	return sr.name


@frappe.whitelist()
def create_stock_entry_from_inbound_gate_pass(gate_pass_name):
	"""
	Create a reverse Material Transfer Stock Entry from an inbound Gate Pass
	This creates a return Stock Entry that reverses the outbound material transfer

	Args:
		gate_pass_name: Name of the Gate Pass

	Returns:
		Name of the created Stock Entry
	"""
	# Check permissions
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(_("You don't have permission to create Stock Entry"))

	# Get Gate Pass
	gate_pass = frappe.get_doc("Gate Pass", gate_pass_name)

	# Validate Gate Pass
	if gate_pass.docstatus != 1:
		frappe.throw(_("Gate Pass must be submitted before creating Stock Entry"))

	if gate_pass.entry_type != "Gate In":
		frappe.throw(_("This function is only available for inbound Gate Passes"))

	if not gate_pass.outbound_material_transfer:
		frappe.throw(
			_("This Gate Pass must reference an outbound Material Transfer to create a return Stock Entry")
		)

	if gate_pass.return_material_transfer:
		frappe.throw(_("Stock Entry has already been created for this Gate Pass"))

	if gate_pass.document_reference != "Stock Entry":
		frappe.throw(_("This Gate Pass is not linked to a Stock Entry"))

	# Get the outbound Stock Entry
	outbound_stock_entry = frappe.get_doc("Stock Entry", gate_pass.outbound_material_transfer)

	# Validate outbound Stock Entry
	if outbound_stock_entry.docstatus != 1:
		frappe.throw(
			_("Outbound Stock Entry {0} must be submitted").format(gate_pass.outbound_material_transfer)
		)

	if not stock_utils.is_material_transfer(outbound_stock_entry):
		frappe.throw(
			_("Outbound Stock Entry {0} must be a Material Transfer").format(
				gate_pass.outbound_material_transfer
			)
		)

	# Create Stock Entry with return flag
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.stock_entry_type = "Material Transfer"
	stock_entry.is_return = 1
	stock_entry.return_against = outbound_stock_entry.name
	stock_entry.company = gate_pass.company
	stock_entry.posting_date = gate_pass.gate_entry_date or nowdate()
	stock_entry.posting_time = gate_pass.gate_entry_time or nowtime()
	stock_entry.set_posting_time = 1

	# Link back to gate pass
	stock_entry.gate_pass = gate_pass_name

	# Copy vehicle details if available (only if fields exist on Stock Entry)
	# Note: These fields may be custom fields added by other apps
	if gate_pass.vehicle_number and hasattr(stock_entry, "vehicle_no"):
		stock_entry.vehicle_no = gate_pass.vehicle_number
	if gate_pass.driver_name and hasattr(stock_entry, "driver_name"):
		stock_entry.driver_name = gate_pass.driver_name
	if gate_pass.driver_contact and hasattr(stock_entry, "driver_contact"):
		stock_entry.driver_contact = gate_pass.driver_contact

	# Create a map of outbound items by order_item_name for reference
	outbound_item_map = {row.name: row for row in outbound_stock_entry.items}

	# Add items from gate pass - reverse the warehouses
	for gate_pass_item in gate_pass.gate_pass_table:
		received_qty = flt(gate_pass_item.received_qty)
		if received_qty <= 0:
			continue

		# Find the corresponding outbound item
		outbound_item = None
		if gate_pass_item.order_item_name:
			outbound_item = outbound_item_map.get(gate_pass_item.order_item_name)

		if not outbound_item:
			frappe.throw(
				_("Could not find corresponding item {0} in outbound Stock Entry {1}").format(
					gate_pass_item.item_code, gate_pass.outbound_material_transfer
				)
			)

		# Reverse warehouses: outbound's t_warehouse becomes source, s_warehouse becomes target
		s_warehouse = outbound_item.t_warehouse  # Where material is coming from (was destination)
		t_warehouse = outbound_item.s_warehouse  # Where material is going to (was source)

		if not s_warehouse or not t_warehouse:
			frappe.throw(
				_("Item {0} in outbound Stock Entry must have both source and target warehouses").format(
					gate_pass_item.item_code
				)
			)

		# Calculate quantities
		conversion_factor = flt(gate_pass_item.conversion_factor) or 1.0
		stock_qty = received_qty * conversion_factor

		# Create Stock Entry item
		se_item = {
			"item_code": gate_pass_item.item_code,
			"item_name": gate_pass_item.item_name or "",
			"description": gate_pass_item.description or "",
			"s_warehouse": s_warehouse,
			"t_warehouse": t_warehouse,
			"qty": received_qty,
			"transfer_qty": stock_qty,
			"uom": gate_pass_item.uom or gate_pass_item.stock_uom,
			"stock_uom": gate_pass_item.stock_uom,
			"conversion_factor": conversion_factor,
			"cost_center": gate_pass_item.cost_center or outbound_item.cost_center,
			"project": gate_pass_item.project or outbound_item.project,
			"basic_rate": flt(gate_pass_item.rate) or flt(outbound_item.basic_rate) or 0,
			"basic_amount": flt(gate_pass_item.amount) or (received_qty * flt(outbound_item.basic_rate) or 0),
		}

		# Copy additional fields from outbound item if they exist
		for field in ["expense_account", "serial_and_batch_bundle", "batch_no", "serial_no"]:
			if hasattr(outbound_item, field) and getattr(outbound_item, field):
				se_item[field] = getattr(outbound_item, field)

		stock_entry.append("items", se_item)

	# Validate that at least one item was added
	if not stock_entry.items:
		frappe.throw(_("No items with positive received quantities found in Gate Pass"))

	# Add doc_references for ITC-04 reporting (required by India Compliance)
	# This references the original outbound Stock Entry
	# The doc_references field is added by India Compliance app and is required
	# for return material transfers to pass validation
	stock_entry.append(
		"doc_references",
		{
			"link_doctype": "Stock Entry",
			"link_name": outbound_stock_entry.name,
		},
	)

	# Set missing values (calculates totals, etc.)
	stock_entry.set_missing_values()

	# Insert the Stock Entry (as draft)
	stock_entry.insert()

	# Update Gate Pass with return Stock Entry reference
	gate_pass.return_material_transfer = stock_entry.name
	gate_pass.stock_entry = stock_entry.name
	# For manual return flows, preserve reference_number pointing to original outbound Stock Entry
	# For non-manual flows, update reference_number to the newly created return Stock Entry
	if not cint(gate_pass.manual_return_flow):
		gate_pass.reference_number = stock_entry.name
	gate_pass.save(ignore_permissions=True)

	return stock_entry.name


# Document Event Handlers for Purchase Receipt, Subcontracting Receipt, and Stock Entry
# ------------------------------------------------------------------------
# Note: Gate Pass is in ignore_links_on_delete (hooks.py) which allows
# Purchase Receipts, Subcontracting Receipts, and Stock Entries to be deleted even when linked.
# These handlers clean up the Gate Pass references when receipts/entries are deleted/cancelled.


def on_purchase_receipt_trash(doc, method):
	"""
	Clear Gate Pass reference when Purchase Receipt is deleted
	"""
	if doc.get("gate_pass"):
		clear_gate_pass_reference(doc.get("gate_pass"), "purchase_receipt")


def on_purchase_receipt_cancel(doc, method):
	"""
	Clear Gate Pass reference when Purchase Receipt is cancelled
	"""
	if doc.get("gate_pass"):
		clear_gate_pass_reference(doc.get("gate_pass"), "purchase_receipt")


def on_subcontracting_receipt_trash(doc, method):
	"""
	Clear Gate Pass reference when Subcontracting Receipt is deleted
	"""
	if doc.get("gate_pass"):
		clear_gate_pass_reference(doc.get("gate_pass"), "subcontracting_receipt")


def on_subcontracting_receipt_cancel(doc, method):
	"""
	Clear Gate Pass reference when Subcontracting Receipt is cancelled
	"""
	if doc.get("gate_pass"):
		clear_gate_pass_reference(doc.get("gate_pass"), "subcontracting_receipt")


def on_stock_entry_cancel(doc, method):
	"""
	Clear Gate Pass references when Stock Entry is cancelled.
	This ensures Gate Passes don't hold references to cancelled Stock Entries.
	"""
	# Clear gate_pass field on Stock Entry (if it exists)
	if doc.get("gate_pass"):
		clear_stock_entry_gate_pass_reference(doc.name, doc.get("gate_pass"))

	# Clear all Gate Pass references to this Stock Entry
	clear_gate_pass_stock_entry_references(doc.name)

	# Cancel auto-created gate passes for this stock entry
	from gate_entry.stock_integration import utils

	utils.cancel_gate_passes_for_stock_entry(doc)


def on_stock_entry_trash(doc, method):
	"""
	Clear Gate Pass references when Stock Entry is deleted.
	This ensures Gate Passes don't hold references to deleted Stock Entries.
	"""
	# Clear gate_pass field on Stock Entry (if it exists)
	if doc.get("gate_pass"):
		clear_stock_entry_gate_pass_reference(doc.name, doc.get("gate_pass"))

	# Clear all Gate Pass references to this Stock Entry
	clear_gate_pass_stock_entry_references(doc.name)


def clear_gate_pass_reference(gate_pass_name, field_name):
	"""
	Clear the receipt reference from Gate Pass

	Args:
		gate_pass_name: Name of the Gate Pass
		field_name: The field name in Gate Pass (purchase_receipt or subcontracting_receipt)
	"""
	if not gate_pass_name or not frappe.db.exists("Gate Pass", gate_pass_name):
		return

	try:
		# Clear the reference field in Gate Pass
		frappe.db.set_value("Gate Pass", gate_pass_name, field_name, None, update_modified=False)

		frappe.msgprint(
			_("Gate Pass {0} has been updated. The receipt reference has been cleared.").format(
				frappe.utils.get_link_to_form("Gate Pass", gate_pass_name)
			)
		)
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(), title=_("Error clearing Gate Pass reference"), exception=e
		)


def clear_stock_entry_gate_pass_reference(stock_entry_name, gate_pass_name):
	"""
	Clear the gate_pass reference from Stock Entry when it's cancelled or deleted.
	This allows the Gate Pass to be cancelled independently and new Gate Passes
	to be created for the same Stock Entry.

	Args:
		stock_entry_name: Name of the Stock Entry
		gate_pass_name: Name of the Gate Pass
	"""
	if not stock_entry_name or not gate_pass_name:
		return

	try:
		# Clear the gate_pass field in Stock Entry
		# This breaks the link so the Gate Pass can be cancelled and new ones can be created
		frappe.db.set_value("Stock Entry", stock_entry_name, "gate_pass", None, update_modified=False)

		frappe.msgprint(
			_("Gate Pass {0} reference has been cleared from Stock Entry {1}.").format(
				frappe.utils.get_link_to_form("Gate Pass", gate_pass_name),
				frappe.utils.get_link_to_form("Stock Entry", stock_entry_name),
			)
		)
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title=_("Error clearing Stock Entry gate pass reference"),
			exception=e,
		)


def clear_gate_pass_stock_entry_references(stock_entry_name):
	"""
	Clear all Gate Pass references to a deleted Stock Entry.
	This includes:
	- reference_number (when document_reference == "Stock Entry")
	- return_material_transfer
	- outbound_material_transfer
	- stock_entry

	Args:
		stock_entry_name: Name of the deleted Stock Entry
	"""
	if not stock_entry_name:
		return

	try:
		# Find all Gate Passes that reference this Stock Entry
		gate_passes = frappe.get_all(
			"Gate Pass",
			filters={
				"docstatus": ["<", 2],  # Not cancelled
			},
			or_filters=[
				["reference_number", "=", stock_entry_name],
				["return_material_transfer", "=", stock_entry_name],
				["outbound_material_transfer", "=", stock_entry_name],
				["stock_entry", "=", stock_entry_name],
			],
			fields=[
				"name",
				"reference_number",
				"return_material_transfer",
				"outbound_material_transfer",
				"stock_entry",
				"document_reference",
			],
		)

		if not gate_passes:
			return

		cleared_count = 0
		for gp in gate_passes:
			updates = {}

			# Clear reference_number if it points to this Stock Entry
			if gp.reference_number == stock_entry_name and gp.document_reference == "Stock Entry":
				updates["reference_number"] = None

			# Clear return_material_transfer if it points to this Stock Entry
			if gp.return_material_transfer == stock_entry_name:
				updates["return_material_transfer"] = None

			# Clear outbound_material_transfer if it points to this Stock Entry
			if gp.outbound_material_transfer == stock_entry_name:
				updates["outbound_material_transfer"] = None

			# Clear stock_entry if it points to this Stock Entry
			if gp.stock_entry == stock_entry_name:
				updates["stock_entry"] = None

			if updates:
				# Update the Gate Pass
				for field, value in updates.items():
					frappe.db.set_value("Gate Pass", gp.name, field, value, update_modified=False)
				cleared_count += 1

		if cleared_count > 0:
			frappe.msgprint(
				_("Cleared Stock Entry {0} references from {1} Gate Pass(es).").format(
					frappe.utils.get_link_to_form("Stock Entry", stock_entry_name),
					cleared_count,
				)
			)
	except Exception as e:
		frappe.log_error(
			message=frappe.get_traceback(),
			title=_("Error clearing Gate Pass Stock Entry references"),
			exception=e,
		)


def on_stock_entry_submit(doc, method):
	"""
	Handle Stock Entry submission - auto-create Gate Pass for eligible entries
	"""
	if doc.doctype != "Stock Entry" or doc.docstatus != 1:
		return

	from gate_entry.stock_integration import utils

	if not utils.is_material_transfer(doc) and not utils.is_send_to_subcontractor(doc):
		return

	if utils.is_material_transfer(doc) and not utils.is_external_transfer(doc):
		# Even without the External Transfer checkbox, if the target warehouse
		# belongs to a DIFFERENT company this is always an external (inter-company)
		# movement and must have a Gate Pass.
		is_intercompany = False
		transfer_to_company = getattr(doc, "transfer_to_company", None)
		party = getattr(doc, "party", None)
		
		if transfer_to_company and transfer_to_company != doc.company:
			is_intercompany = True
		elif party and party != doc.company:
			is_intercompany = True
		else:
			for item in doc.items:
				if item.t_warehouse:
					wh_company = frappe.db.get_value("Warehouse", item.t_warehouse, "company")
					if wh_company and wh_company != doc.company:
						is_intercompany = True
						break
		if not is_intercompany:
			return

	frappe.enqueue(
		utils.create_gate_pass_from_stock_entry,
		stock_entry_name=doc.name,
		enqueued_by=frappe.session.user,
		queue="long",
		now=frappe.flags.in_test,
	)


# Document Event Handlers for Stock Entry
# ------------------------------------------------------------------------
