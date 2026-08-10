const fs = require('fs');
let code = fs.readFileSync('gate_entry/public/js/gate_pass_custom_ui_backup.js', 'utf8');

// 1. Add get_ui_items()
const getUiItemsStr = `
	get_ui_items() {
		let grouped = {};
		this.items.forEach((item, index) => {
			if (!grouped[item.item_code]) {
				grouped[item.item_code] = {
					...item,
					ordered_qty: 0,
					received_qty: 0,
					dispatched_qty: 0,
					pending_qty: 0,
					amount: 0,
					backend_indices: []
				};
			}
			grouped[item.item_code].ordered_qty += parseFloat(item.ordered_qty || 0);
			grouped[item.item_code].received_qty += parseFloat(item.received_qty || 0);
			grouped[item.item_code].dispatched_qty += parseFloat(item.dispatched_qty || 0);
			grouped[item.item_code].pending_qty += parseFloat(item.pending_qty || 0);
			grouped[item.item_code].amount += parseFloat(item.amount || 0);
			grouped[item.item_code].backend_indices.push(index);
		});
		return Object.values(grouped);
	}

	render_editable_items() {`;
code = code.replace('render_editable_items() {', getUiItemsStr);

// 2. Use get_ui_items() instead of this.items in render_editable_items
code = code.replace(/const items_html = this\.items\s*\n\s*\.map/g, 'const items_html = this.get_ui_items().map');

// 3. Use get_ui_items() instead of this.items in render_outbound_items
code = code.replace(/\$\{this\.items\s*\n\s*\.map/g, '${this.get_ui_items().map');

// 4. Update render_item_row to show multiple batch badges
const oldBadge = 'const batch_badge = item.batch_no\n\t\t\t? `<span class="badge badge-warning" style="font-size:9px;margin-left:4px;" title="Batch: ${frappe.utils.escape_html(item.batch_no)}">🏷 ${frappe.utils.escape_html(item.batch_no)}</span>`\n\t\t\t: "";';
const newBadge = `const batch_badge = item.backend_indices && item.backend_indices.length > 0
			? item.backend_indices.map(idx => {
				let b = this.items[idx].batch_no;
				return b ? \`<span class="badge badge-warning" style="font-size:9px;margin-left:4px;margin-bottom:2px;display:inline-block;" title="Batch: \${frappe.utils.escape_html(b)}">🏷 \${frappe.utils.escape_html(b)}</span>\` : '';
			  }).join('')
			: (item.batch_no ? \`<span class="badge badge-warning" style="font-size:9px;margin-left:4px;margin-bottom:2px;display:inline-block;" title="Batch: \${frappe.utils.escape_html(item.batch_no)}">🏷 \${frappe.utils.escape_html(item.batch_no)}</span>\` : '');`;
code = code.replace(oldBadge, newBadge);

// 5. Update updateQuantity
const oldUpdateQuantity = `	updateQuantity(index, value) {
		if (!this.shouldAllowQuantityEdit()) {
			return;
		}

		if (index < 0 || index >= this.items.length) {
			return;
		}

		const quantityField = this.getQuantityField();

		if (value < 0) {
			frappe.msgprint(
				this.isGateIn()
					? __("Quantity cannot be negative.")
					: __("Dispatched quantity cannot be negative.")
			);
			this.render();
			return;
		}

		const item = this.items[index];
		const orderedQty = parseFloat(item.ordered_qty || 0);
		const isRateContract = item.is_rate_contract || 0;

		if (this.isGateIn()) {
			const pendingQty = parseFloat(item.pending_qty || Math.max(orderedQty - value, 0));
			if (!isRateContract && value > pendingQty && pendingQty > 0) {
				frappe.msgprint({
					title: __("Over Receipt Warning"),
					message: __("You are receiving more than the pending quantity ({0} {1})", [
						pendingQty,
						item.uom,
					]),
					indicator: "orange",
				});
			}
		} else if (value > orderedQty && orderedQty > 0) {
			frappe.msgprint({
				title: __("Over Dispatch Warning"),
				message: __("You are dispatching more than the available quantity ({0})", [
					orderedQty,
				]),
				indicator: "orange",
			});
		}

		item[quantityField] = value;
		item.pending_qty = Math.max(orderedQty - value, 0);
		item.amount = value * (parseFloat(item.rate) || 0);

		this.sync_to_child_table();
		this.render();
	}`;

const newUpdateQuantity = `	updateQuantity(index, value) {
		if (!this.shouldAllowQuantityEdit()) return;

		let ui_items = this.get_ui_items();
		if (index < 0 || index >= ui_items.length) return;

		const quantityField = this.getQuantityField();
		if (value < 0) {
			frappe.msgprint(this.isGateIn() ? __("Quantity cannot be negative.") : __("Dispatched quantity cannot be negative."));
			this.render();
			return;
		}

		const ui_item = ui_items[index];
		const orderedQty = parseFloat(ui_item.ordered_qty || 0);
		const isRateContract = ui_item.is_rate_contract || 0;

		if (this.isGateIn()) {
			const pendingQty = parseFloat(ui_item.pending_qty || Math.max(orderedQty - value, 0));
			if (!isRateContract && value > pendingQty && pendingQty > 0) {
				frappe.msgprint({ title: __("Over Receipt Warning"), message: __("You are receiving more than the pending quantity ({0} {1})", [pendingQty, ui_item.uom]), indicator: "orange" });
			}
		} else if (value > orderedQty && orderedQty > 0) {
			frappe.msgprint({ title: __("Over Dispatch Warning"), message: __("You are dispatching more than the available quantity ({0})", [orderedQty]), indicator: "orange" });
		}

		let remaining_qty = value;
		ui_item.backend_indices.forEach(idx => {
			this.items[idx][quantityField] = 0;
			this.items[idx].amount = 0;
			this.items[idx].pending_qty = this.items[idx].ordered_qty;
		});
		
		ui_item.backend_indices.forEach(idx => {
			const item = this.items[idx];
			const max_for_this = this.isStockEntry() ? item.ordered_qty : item.pending_qty;
			const assign = Math.min(remaining_qty, max_for_this);
			item[quantityField] = assign;
			remaining_qty = Math.max(0, remaining_qty - assign);
			item.amount = assign * (parseFloat(item.rate) || 0);
			item.pending_qty = Math.max(item.ordered_qty - assign, 0);
		});

		this.sync_to_child_table();
		this.render();
	}`;
code = code.replace(oldUpdateQuantity, newUpdateQuantity);

// 6. Update validateQuantityInput
const oldValidate = `	validateQuantityInput(index, value, input_element) {
		if (!this.shouldAllowQuantityEdit()) {
			return;
		}

		const item = this.items[index];
		const orderedQty = parseFloat(item.ordered_qty || 0);
		const isRateContract = item.is_rate_contract || 0;

		input_element.removeClass("text-danger text-warning");

		if (value < 0) {
			input_element.addClass("text-danger");
			return;
		}

		if (this.isGateIn()) {
			const pendingQty = parseFloat(item.pending_qty || Math.max(orderedQty - value, 0));
			if (!isRateContract && value > pendingQty && pendingQty > 0) {
				input_element.addClass("text-warning");
			}
		} else if (value > orderedQty && orderedQty > 0) {
			input_element.addClass("text-warning");
		}
	}`;

const newValidate = `	validateQuantityInput(index, value, input_element) {
		if (!this.shouldAllowQuantityEdit()) return;

		let ui_items = this.get_ui_items();
		if (index < 0 || index >= ui_items.length) return;

		const ui_item = ui_items[index];
		const orderedQty = parseFloat(ui_item.ordered_qty || 0);
		const isRateContract = ui_item.is_rate_contract || 0;

		input_element.removeClass("text-danger text-warning");
		if (value < 0) { input_element.addClass("text-danger"); return; }

		if (this.isGateIn()) {
			const pendingQty = parseFloat(ui_item.pending_qty || Math.max(orderedQty - value, 0));
			if (!isRateContract && value > pendingQty && pendingQty > 0) input_element.addClass("text-warning");
		} else if (value > orderedQty && orderedQty > 0) {
			input_element.addClass("text-warning");
		}
	}`;
code = code.replace(oldValidate, newValidate);

// 7. Update remove_item
const oldRemove = `	remove_item(index) {
		if (!this.isGateIn()) {
			return;
		}

		const item = this.items[index];

		frappe.confirm(__("Are you sure you want to remove {0}?", [item.item_code]), () => {
			this.items.splice(index, 1);
			this.sync_to_child_table();
			this.render();
			frappe.show_alert({
				message: __("Item removed successfully"),
				indicator: "green",
			});
		});
	}`;

const newRemove = `	remove_item(index) {
		if (!this.isGateIn()) return;
		let ui_items = this.get_ui_items();
		if (index < 0 || index >= ui_items.length) return;
		
		const item = ui_items[index];
		frappe.confirm(__("Are you sure you want to remove {0}?", [item.item_code]), () => {
			if (item.backend_indices) {
				item.backend_indices.sort((a, b) => b - a).forEach(idx => this.items.splice(idx, 1));
			} else {
				this.items.splice(index, 1);
			}
			this.sync_to_child_table();
			this.render();
			frappe.show_alert({ message: __("Item removed successfully"), indicator: "green" });
		});
	}`;
code = code.replace(oldRemove, newRemove);

// 8. Update show_item_details
const oldShowItem = `	show_item_details(index) {
		const item = this.items[index];
		const is_rate_contract = item.is_rate_contract || 0;
		const is_outbound = this.isOutbound();
		let fields = [
			{
				fieldtype: "Data",
				fieldname: "item_code",
				label: __("Item Code"),
				read_only: 1,
				default: item.item_code,
			},
			{
				fieldtype: "Data",
				fieldname: "item_name",
				label: __("Item Name"),
				read_only: 1,
				default: item.item_name,
			},
			{
				fieldtype: "Data",
				fieldname: "batch_no",
				label: __("Batch No"),
				read_only: 1,
				default: item.batch_no || "—",
			},
			{
				fieldtype: "Small Text",
				fieldname: "description",
				label: __("Description"),
				read_only: 1,
				default: item.description || "N/A",
			},
			{
				fieldtype: "Column Break",
			},
			{
				fieldtype: "Data",
				fieldname: "uom",
				label: __("UOM"),
				read_only: 1,
				default: item.uom || "N/A",
			},
		];

		// Add order type information
		if (is_outbound) {
			fields.push({
				fieldtype: "Float",
				fieldname: "dispatched_qty",
				label: __("Dispatched Qty"),
				read_only: 1,
				default: item.dispatched_qty || 0,
			});
		} else {
			if (is_rate_contract) {
				fields.push({
					fieldtype: "Data",
					fieldname: "order_type",
					label: __("Order Type"),
					read_only: 1,
					default: "Rate Contract",
				});
			} else {
				fields.push({
					fieldtype: "Float",
					fieldname: "ordered_qty",
					label: __("Ordered Qty"),
					read_only: 1,
					default: item.ordered_qty || 0,
				});
				fields.push({
					fieldtype: "Float",
					fieldname: "pending_qty",
					label: __("Pending Qty"),
					read_only: 1,
					default: item.pending_qty || 0,
				});
			}

			fields.push({
				fieldtype: "Float",
				fieldname: "received_qty",
				label: __("Received Qty"),
				read_only: 1,
				default: item.received_qty || 0,
			});
		}

		const dialog = new frappe.ui.Dialog({
			title: __("Item Details"),
			fields: fields,
		});

		dialog.show();
	}`;

const newShowItem = `	show_item_details(index) {
		let ui_items = this.get_ui_items();
		if (index < 0 || index >= ui_items.length) return;
		
		const item = ui_items[index];
		const is_rate_contract = item.is_rate_contract || 0;
		const is_outbound = this.isOutbound();
		
		let batches_html = "";
		if (item.backend_indices && item.backend_indices.length > 1) {
			batches_html = \`<div style="margin-top: 15px;">
				<h6><i class="fa fa-cubes text-muted"></i> Roll / Batch Details</h6>
				<table class="table table-bordered table-condensed" style="font-size: 11px;">
					<thead>
						<tr>
							<th>Batch No</th>
							<th class="text-right">Ordered Qty</th>
							<th class="text-right">\${this.getQuantityLabel()}</th>
						</tr>
					</thead>
					<tbody>
						\${item.backend_indices.map(idx => {
							let b_item = this.items[idx];
							return \\\`<tr>
								<td>\\\${b_item.batch_no || '-'}</td>
								<td class="text-right">\\\${parseFloat(b_item.ordered_qty || 0).toFixed(2)}</td>
								<td class="text-right">\\\${parseFloat(b_item[this.getQuantityField()] || 0).toFixed(2)}</td>
							</tr>\\\`;
						}).join('')}
					</tbody>
				</table>
			</div>\`;
		}

		let fields = [
			{ fieldtype: "Data", fieldname: "item_code", label: __("Item Code"), read_only: 1, default: item.item_code },
			{ fieldtype: "Data", fieldname: "item_name", label: __("Item Name"), read_only: 1, default: item.item_name },
			{ fieldtype: "Data", fieldname: "batch_no", label: __("Batch No"), read_only: 1, default: item.batch_no || "N/A", hidden: item.backend_indices && item.backend_indices.length > 1 ? 1 : 0 },
			{ fieldtype: "Small Text", fieldname: "description", label: __("Description"), read_only: 1, default: item.description || "N/A" },
			{ fieldtype: "Data", fieldname: "uom", label: __("UOM"), read_only: 1, default: item.uom || "N/A" },
			{ fieldtype: "Float", fieldname: "ordered_qty", label: __("Ordered Qty"), read_only: 1, default: item.ordered_qty || 0 },
			{ fieldtype: "Float", fieldname: "quantity", label: is_outbound ? __("Dispatched Qty") : __("Received Qty"), read_only: 1, default: this.getQuantityValue(item) },
			{ fieldtype: "HTML", fieldname: "batches_html", options: batches_html }
		];
		
		const dialog = new frappe.ui.Dialog({
			title: __("Item Details"),
			fields: fields,
		});

		dialog.show();
	}`;
code = code.replace(oldShowItem, newShowItem);

fs.writeFileSync('gate_entry/public/js/gate_pass_custom_ui.js', code, 'utf8');
console.log('Successfully updated gate_pass_custom_ui.js using regex/replace!');
