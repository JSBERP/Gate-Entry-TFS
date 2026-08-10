"""Module page configuration for Gate Entry."""

from __future__ import annotations

from frappe import _


def get_data() -> list[dict[str, object]]:
	"""Return the Gate Entry module definition for the Desk."""

	return [
		{
			"label": _("Transactions"),
			"items": [
				{
					"type": "doctype",
					"name": "Gate Pass",
					"label": _("Gate Pass"),
					"description": _("Create and manage gate passes."),
				}
			],
		},
		{
			"label": _("Reports"),
			"items": [
				{
					"type": "report",
					"name": "Pending Gate Passes",
					"doctype": "Gate Pass",
					"label": _("Pending Gate Passes"),
					"is_query_report": False,
					"report_type": "Script Report",
				},
				{
					"type": "report",
					"name": "Gate Register",
					"doctype": "Gate Pass",
					"label": _("Gate Register"),
					"is_query_report": False,
					"report_type": "Script Report",
				},
				{
					"type": "report",
					"name": "Material Reconciliation",
					"doctype": "Gate Pass",
					"label": _("Material Reconciliation"),
					"is_query_report": False,
					"report_type": "Script Report",
				},
			],
		},
	]
