"""Desktop module registration for Gate Entry."""

from __future__ import annotations

from frappe import _


def get_data() -> list[dict[str, object]]:
	"""Return module definition for the Desk."""

	return [
		{
			"module_name": "Gate Entry",
			"category": "Modules",
			"label": _("Gate Entry"),
			"color": "blue",
			"icon": "fa fa-shield",
			"type": "module",
		}
	]
