frappe.query_reports["Gate Register"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "entry_type",
			label: __("Entry Type"),
			fieldtype: "Select",
			options: ["", "Gate In", "Gate Out"],
		},
		{
			fieldname: "document_reference",
			label: __("Source Type"),
			fieldtype: "Select",
			options: [
				"",
				"Purchase Order",
				"Subcontracting Order",
				"Sales Invoice",
				"Delivery Note",
				"Stock Entry",
			],
		},
		{
			fieldname: "stock_entry_type",
			label: __("Stock Entry Type"),
			fieldtype: "Select",
			options: ["", "Material Transfer", "Send to Subcontractor"],
			depends_on: "eval:doc.document_reference == 'Stock Entry'",
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			depends_on: "eval:doc.document_reference == 'Stock Entry'",
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "vehicle_number",
			label: __("Vehicle Number"),
			fieldtype: "Data",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_default("company"),
		},
	],
};
