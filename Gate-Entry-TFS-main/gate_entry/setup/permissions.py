"""Helper routines for configuring security guard permissions."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.permissions import add_permission, update_permission_property

from gate_entry.constants import OUTBOUND_REFERENCES

SECURITY_GUARD_ROLE = "Security Guard"


def ensure_security_guard_role() -> None:
	"""Create (or update) the Security Guard role with the expected defaults."""

	role = frappe.db.exists("Role", SECURITY_GUARD_ROLE)
	if role:
		update_role_defaults(SECURITY_GUARD_ROLE)
		return

	doc = frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": SECURITY_GUARD_ROLE,
			"desk_access": 0,
			"restrict_to_domain": "",
			"is_custom": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.msgprint(_("Created Security Guard role"), alert=True)


def update_role_defaults(role_name: str) -> None:
	"""Ensure the role keeps the hardened defaults we expect."""

	frappe.db.set_value("Role", role_name, "desk_access", 0)


def ensure_security_guard_permissions() -> None:
	"""Grant the minimal access that guard workflows require."""

	ensure_security_guard_role()

	for doctype in sorted(OUTBOUND_REFERENCES):
		add_read_only_permission(doctype, SECURITY_GUARD_ROLE)

	# Guards need the e-way bill threshold from GST Settings to evaluate compliance.
	# Only add permissions if GST Settings doctype is available.
	if frappe.db.exists("DocType", "GST Settings"):
		add_read_only_permission("GST Settings", SECURITY_GUARD_ROLE)


def add_read_only_permission(doctype: str, role: str, permlevel: int = 0) -> None:
	"""Create or harden a read-only DocPerm for the given role."""

	add_permission(doctype, role, permlevel=permlevel)

	if not frappe.db.table_exists("Custom DocPerm"):
		return

	properties = (
		"write",
		"create",
		"delete",
		"submit",
		"cancel",
		"amend",
		"print",
		"email",
		"share",
		"export",
		"import",
	)

	for prop in properties:
		if not frappe.db.has_column("Custom DocPerm", prop):
			continue

		try:
			update_permission_property(doctype, role, permlevel, prop, 0)
		except Exception as exc:
			# Some deployments omit certain permission columns; ignore those gracefully.
			if exc.args and exc.args[0] == 1054:
				print(f"Column {prop} does not exist in Custom DocPerm")
				continue
			raise
