frappe.query_reports["Pending Gate Passes"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
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
			label: __("Reference Type"),
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
			fieldname: "stock_entry",
			label: __("Stock Entry"),
			fieldtype: "Link",
			options: "Stock Entry",
			get_query: function () {
				return {
					filters: {
						docstatus: ["!=", 2],
					},
				};
			},
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_default("company"),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, column, data);

		if (column.fieldname === "aging" && data && data.aging !== undefined) {
			const color =
				data.aging_color ||
				(data.aging <= 0 ? "green" : data.aging <= 1 ? "orange" : "red");
			value = `<span class="indicator-pill ${color}">${data.aging}</span>`;
		} else if (column.fieldname === "compliance_status" && data) {
			const statusText = frappe.utils.escape_html(value || "-");
			const statusColor = data.compliance_pending ? "red" : "green";
			value = `<span class="indicator-pill ${statusColor}">${statusText}</span>`;
		}

		return value;
	},
};
