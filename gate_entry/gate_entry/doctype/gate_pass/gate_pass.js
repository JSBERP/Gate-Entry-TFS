// Copyright (c) 2025, Gurudatt Kulkarni and contributors
// For license information, please see license.txt
const STOCK_ENTRY_REFERENCE = "Stock Entry";
const INBOUND_REFERENCES = ["Purchase Order", "Subcontracting Order"];
const OUTBOUND_REFERENCES = ["Sales Invoice", "Delivery Note"];
const DOCUMENT_REFERENCES = [
	...new Set([...INBOUND_REFERENCES, ...OUTBOUND_REFERENCES, STOCK_ENTRY_REFERENCE]),
];

// Inter-company Gate In (Stock Entry, Material Transfer): company → destination warehouse.
// Keep in sync with INTERCOMPANY_MATERIAL_TRANSFER_DEST_WAREHOUSE_MAP in gate_entry/constants.py.
const INTERCOMPANY_WAREHOUSE_MAP = {
	"J Vasanth Exports": "Finished Goods Warehouse - JVE",
	"Thusma SMS Nonwovens Private Limited - 1Z0": "Finished Goods Warehouse - TSNPL",
	"Jayashree Spun Bond - 2ZS": "Finished Goods - JSB-2ZS",
	"Thusma SMS Nonwoven Private Limited - 2ZZ": "Finished Goods Warehouse - TSNPL-2ZZ",
	"Varshine Tex (Odisha)": "Finished Goods Warehouse - VTO",
	"Thusma T Tex": "Finished Goods Warehouse - TTT",
	"Varshine Retails Private Limited": "Finished Goods Warehouse - VRPL",
	"Varshine Tex (Puducherry)": "Raw Materials Warehouse  - VTP",
};

const THUSMA_JOB_WORK_COMPANY = "Thusma SMS Nonwovens Private Limited - 1Z0";
const JSB_JOB_WORK_COMPANIES = new Set([
	"Jayashree Spun Bond - 1ZT",
	"Jayashree Spun Bond - 2ZS",
]);

// (receiving GP company, STE sender company) → warehouse. Keep in sync with constants.py
const JOB_WORK_DEST_WAREHOUSE_MAP = {
	[THUSMA_JOB_WORK_COMPANY + "|Jayashree Spun Bond - 1ZT"]: "Jayashree 1ZT - JWO RM - TSNPL",
	[THUSMA_JOB_WORK_COMPANY + "|Jayashree Spun Bond - 2ZS"]: "Jayashree 2ZS - JWO RM - TSNPL",
	["Jayashree Spun Bond - 1ZT|" + THUSMA_JOB_WORK_COMPANY]: "Thusma 1Z0 - JWI FG - JSB-1ZT",
	["Jayashree Spun Bond - 2ZS|" + THUSMA_JOB_WORK_COMPANY]: "Thusma 1Z0 - JWI FG - JSB-2ZS",
};
// Backwards-compatible alias
const JOB_WORK_IN_DEST_WAREHOUSE_MAP = JOB_WORK_DEST_WAREHOUSE_MAP;

const MATERIAL_TRANSFER_STOCK_ENTRY_TYPE = "Material Transfer";

// The transit warehouse used by JSB for inter-company transfers
const JSB_TRANSIT_WAREHOUSE = "Goods In Transit - JSB-1ZT";

frappe.ui.form.on("Gate Pass", {
	onload_post_render(frm) {
		// Initialize the custom UI component after form is fully rendered
		if (!frm.gate_pass_ui && window.GatePassCustomUI) {
			frm.gate_pass_ui = new window.GatePassCustomUI(frm);
		}
	},

	async refresh(frm) {
		// Initialize the custom UI component if not already done
		if (!frm.gate_pass_ui && window.GatePassCustomUI) {
			frm.gate_pass_ui = new window.GatePassCustomUI(frm);
		} else if (frm.gate_pass_ui) {
			// Refresh the UI to show updated data
			frm.gate_pass_ui.refresh();
		}

		// Auto-populate security guard name with current user
		if (frm.is_new() && !frm.doc.security_guard_name) {
			frm.set_value(
				"security_guard_name",
				frappe.session.user_fullname || frappe.session.user
			);
		}

		// Auto-populate gate pass date and time
		if (frm.is_new() && !frm.doc.gate_pass_date) {
			frm.set_value("gate_pass_date", frappe.datetime.get_today());
			frm.set_value("gate_pass_time", frappe.datetime.now_time());
		}

		// Auto-populate gate entry date and time
		if (frm.is_new() && !frm.doc.gate_entry_date) {
			frm.set_value("gate_entry_date", frappe.datetime.get_today());
			frm.set_value("gate_entry_time", frappe.datetime.now_time());
		}

		// Hide the gate_pass_table field (it's for backend only)
		frm.toggle_display("gate_pass_table", false);

		frm.set_query("outbound_material_transfer", () => ({
			filters: {
				docstatus: 1,
				stock_entry_type: ["in", ["Material Transfer", "Send to Subcontractor"]],
				ge_external_transfer: 1,
			},
		}));

		frm.set_query("return_material_transfer", () => ({
			filters: {
				docstatus: 1,
				stock_entry_type: ["in", ["Material Transfer"]],
				is_return: 1,
			},
		}));

		frm.set_query("stock_entry_reference", () => ({
			filters: {
				docstatus: 1,
				name: frm.doc.reference_number || undefined,
			},
		}));

		toggle_stock_entry_link_permissions(frm);
		toggle_discrepancy_fields(frm);
		show_stock_entry_guidance(frm);

		// Show "Create Receipt" and "Create Stock Entry" buttons after submission
		if (frm.doc.docstatus === 1) {
			setup_receipt_buttons(frm);
			await setup_stock_entry_button(frm);
		}

		// Filter Document Reference to show only relevant doctypes
		frm.set_query("document_reference", function () {
			return {
				filters: {
					name: ["in", DOCUMENT_REFERENCES],
				},
			};
		});

		if (frm.is_new()) {
			frm.add_custom_button(__("Scan QR"), function() {
				if (frappe.ui.Scanner) {
					new frappe.ui.Scanner({
						dialog: true,
						multiple: false,
						on_scan(data) {
							if (data && data.decodedText) {
								process_qr_scan(frm, data.decodedText);
							} else if (typeof data === "string") {
								process_qr_scan(frm, data);
							}
						}
					});
				} else {
					let d = new frappe.ui.Dialog({
						title: 'Scan QR Code',
						fields: [{
							label: 'Scan Here',
							fieldname: 'qr_data',
							fieldtype: 'Small Text'
						}],
						primary_action_label: 'Apply',
						primary_action(values) {
							process_qr_scan(frm, values.qr_data);
							d.hide();
						}
					});
					d.show();
				}
			}, __("Actions"));
		}

		if (!frm.doc.driver_photo && frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Capture Driver Photo"), function() {
				capture_driver_photo(frm);
			}, __("Actions"));
		}

		refresh_compliance_status(frm);
	},
	onload(frm) {
		frm.set_query("document_reference", function () {
			return {
				filters: {
					name: ["in", DOCUMENT_REFERENCES],
				},
			};
		});
	},

	before_submit(frm) {
		if (!frm.doc.driver_photo) {
			frappe.msgprint(__("Driver Photo is mandatory before submitting."));
			frappe.validated = false;
			return;
		}

		// PO Gate In: require location via modern warehouse picker before submit
		if (
			frm.doc.document_reference === "Purchase Order" &&
			!frm.doc.location &&
			!frm._po_location_confirmed
		) {
			frappe.validated = false;
			show_po_location_picker(frm).then((warehouse) => {
				if (!warehouse) {
					return;
				}
				frm._po_location_confirmed = true;
				frm.set_value("location", warehouse).then(() => {
					(frm.doc.gate_pass_table || []).forEach((row) => {
						if (parseFloat(row.received_qty || 0) > 0) {
							frappe.model.set_value(row.doctype, row.name, "warehouse", warehouse);
						}
					});
					frm.save("Submit");
				});
			});
		}
	},

	before_save(frm) {
		let vehicle_changed = frm.doc.fetched_vehicle_number && frm.doc.vehicle_number !== frm.doc.fetched_vehicle_number;
		let driver_changed = frm.doc.fetched_driver_name && frm.doc.driver_name !== frm.doc.fetched_driver_name;
		
		if ((vehicle_changed || driver_changed) && !frm.doc.driver_change_remarks) {
			frappe.validated = false;
			frappe.prompt({
				label: __('Reason for changing Driver/Vehicle'),
				fieldname: 'remarks',
				fieldtype: 'Small Text',
				reqd: 1
			}, (values) => {
				frm.set_value('driver_change_remarks', values.remarks);
				frm.save();
			}, __('Remarks Required'), __('Save'));
		}
	},

	after_save(frm) {
		// Reload the form to ensure child table data is properly loaded
		// Then refresh the custom UI
		frappe.after_ajax(() => {
			if (frm.gate_pass_ui) {
				frm.gate_pass_ui.refresh();
			}
		});
	},

	document_reference(frm) {
		// Clear reference number when document reference changes
		if (frm.doc.reference_number) {
			frm.set_value("reference_number", "");
		}
		if (frm.doc.document_reference) {
			frm.set_query("reference_number", function () {
				console.log("On document_reference change Reference Number Filter Applied");
				let filters = { docstatus: 1 };
				if (
					frm.doc.document_reference === "Purchase Order" ||
					frm.doc.document_reference === "Subcontracting Order"
				) {
					filters["status"] = ["!=", "Closed"];
				} else if (frm.doc.document_reference === "Stock Entry") {
					filters["stock_entry_type"] = [
						"in",
						["Material Transfer", "Send to Subcontractor"],
					];
				}
				return { filters: filters };
			});
		}

		// Update entry type locally for better UX
		if (frm.doc.document_reference === STOCK_ENTRY_REFERENCE) {
			// If the selected company is a known inter-company RECEIVER (e.g. JVE receiving from JSB),
			// default entry_type to Gate In. Otherwise default to Gate Out (sender side).
			const is_intercompany_receiver = !!INTERCOMPANY_WAREHOUSE_MAP[frm.doc.company];
			frm.set_value("entry_type", is_intercompany_receiver ? "Gate In" : "Gate Out");
			frm.set_value("supplier", null);
			frm.set_value("supplier_delivery_note", null);
		} else if (is_outbound_reference(frm.doc.document_reference, "Gate Out")) {
			frm.set_value("entry_type", "Gate Out");
			frm.set_value("supplier", null);
			frm.set_value("supplier_delivery_note", null);
		} else {
			frm.set_value("entry_type", "Gate In");
		}

		// Clear items when document type changes
		frm.clear_table("gate_pass_table");
		frm.refresh_field("gate_pass_table");

		frm.set_value("outbound_material_transfer", null);
		frm.set_value("return_material_transfer", null);
		frm.set_value("has_discrepancy", 0);
		frm.set_value("lost_quantity", 0);
		frm.set_value("damaged_quantity", 0);
		frm.set_value("discrepancy_notes", null);

		toggle_stock_entry_link_permissions(frm);
		toggle_discrepancy_fields(frm);

		clear_compliance_status(frm);

		// Refresh custom UI
		if (frm.gate_pass_ui) {
			frm.gate_pass_ui.refresh();
		}
	},

	reference_number(frm) {
		// Fetch address display from reference document
		if (frm.doc.document_reference && frm.doc.reference_number) {
			load_reference_details(frm);

			if (frm.doc.document_reference === STOCK_ENTRY_REFERENCE) {
				suggest_stock_entry_entry_type(frm).then(() => load_reference_items(frm));
				clear_compliance_status(frm);
			} else if (is_outbound_reference(frm.doc.document_reference, frm.doc.entry_type, frm)) {
				load_reference_items(frm);
				refresh_compliance_status(frm);
			} else {
				clear_compliance_status(frm);
			}
		} else {
			frm._referenced_stock_entry_type = null;
			clear_compliance_status(frm);
			// Only refresh when reference is cleared; avoid wiping items mid-load.
			if (frm.gate_pass_ui) {
				frm.gate_pass_ui.render();
			}
		}
	},

	has_discrepancy(frm) {
		toggle_discrepancy_fields(frm);
	},

	manual_return_flow(frm) {
		if (frm.doc.manual_return_flow) {
			frm.set_value("entry_type", "Gate In");
			show_stock_entry_guidance(frm);
		} else {
			frm.trigger("document_reference");
			show_stock_entry_guidance(frm);
		}
	},

	stock_entry_reference(frm) {
		// keep guidance in sync
		show_stock_entry_guidance(frm);
	},

	company(frm) {
		auto_set_intercompany_warehouses(frm);
		show_job_work_warehouse_hint(frm);
	},

	entry_type(frm) {
		if (frm.doc.document_reference === STOCK_ENTRY_REFERENCE && frm.doc.reference_number) {
			load_reference_items(frm);
		}
		show_job_work_warehouse_hint(frm);
	},
});

function capture_driver_photo(frm) {
	let d = new frappe.ui.Dialog({
		title: __("Capture Driver Photo"),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "video_container",
				options: `
					<div style="text-align:center;">
						<video id="driver_video" width="100%" height="auto" autoplay playsinline></video>
						<canvas id="driver_canvas" style="display:none;"></canvas>
					</div>
				`
			}
		],
		primary_action_label: __("Capture & Attach"),
		primary_action: function() {
			let video = document.getElementById("driver_video");
			let canvas = document.getElementById("driver_canvas");
			if (!video || !canvas) return;
			
			canvas.width = video.videoWidth;
			canvas.height = video.videoHeight;
			canvas.getContext("2d").drawImage(video, 0, 0);
			
			let data_url = canvas.toDataURL("image/jpeg");
			let filename = "Driver_Photo_" + frappe.datetime.now_datetime().replace(/[-:\s]/g, "") + ".jpg";
			
			fetch(data_url)
				.then(res => res.blob())
				.then(blob => {
					let file = new File([blob], filename, { type: "image/jpeg" });
					
					// Upload using frappe upload API
					new frappe.ui.FileUploader({
						files: [file],
						doctype: frm.doc.doctype,
						docname: frm.doc.name,
						fieldname: "driver_photo",
						is_private: 0,
						on_success: (file_doc) => {
							frm.set_value("driver_photo", file_doc.file_url);
							d.hide();
						}
					});
				});
		}
	});

	d.on_page_show = () => {
		let video = document.getElementById("driver_video");
		if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
			navigator.mediaDevices.getUserMedia({ video: true })
				.then(stream => {
					video.srcObject = stream;
					d.stream = stream;
				})
				.catch(err => {
					frappe.msgprint(__("Camera access denied or not available."));
				});
		} else {
			frappe.msgprint(__("Camera API not supported in this browser."));
		}
	};

	d.onhide = () => {
		if (d.stream) {
			d.stream.getTracks().forEach(track => track.stop());
		}
	};

	d.show();
}

function is_inbound_entry_type(entry_type) {
	const et = (entry_type || "").trim();
	return et === "Gate In" || et === "Job Work In";
}

function is_outbound_entry_type(entry_type) {
	const et = (entry_type || "").trim();
	return et === "Gate Out" || et === "Job Work Out";
}

function is_job_work_participant(company) {
	const c = (company || "").trim();
	return c === THUSMA_JOB_WORK_COMPANY || JSB_JOB_WORK_COMPANIES.has(c);
}

function is_job_work_company_pair(company_a, company_b) {
	const a = (company_a || "").trim();
	const b = (company_b || "").trim();
	if (!a || !b || a === b) return false;
	return is_job_work_participant(a) && is_job_work_participant(b);
}

function is_job_work_receiver_gate_pass(frm) {
	if (frm.doc.document_reference !== STOCK_ENTRY_REFERENCE) {
		return false;
	}
	if ((frm.doc.entry_type || "").trim() !== "Job Work Out") {
		return false;
	}
	const gp = (frm.doc.company || "").trim();
	const ste = (frm._referenced_ste_company || "").trim();
	const receiver = (frm._referenced_ste_receiver || "").trim();
	if (!gp || !ste || gp === ste) {
		return false;
	}
	return is_job_work_company_pair(gp, ste) || is_job_work_company_pair(gp, receiver);
}

function is_inbound_stock_entry_gate_pass(frm) {
	return (
		frm.doc.document_reference === STOCK_ENTRY_REFERENCE &&
		(is_inbound_entry_type(frm.doc.entry_type) || is_job_work_receiver_gate_pass(frm))
	);
}

function resolve_job_work_dest_warehouse(gp_company, ste_sender_company) {
	const key = (gp_company || "").trim() + "|" + (ste_sender_company || "").trim();
	return JOB_WORK_DEST_WAREHOUSE_MAP[key] || "";
}

function show_job_work_warehouse_hint(frm) {
	if (frm.doc.document_reference !== STOCK_ENTRY_REFERENCE || !frm.doc.company) {
		return;
	}
	const ste = (frm._referenced_ste_company || "").trim();
	const receiver = (frm._referenced_ste_receiver || "").trim();
	const gp = (frm.doc.company || "").trim();

	if (frm.doc.entry_type === "Job Work In" && ste) {
		const wh = resolve_job_work_dest_warehouse(gp, ste);
		if (wh) {
			frappe.show_alert({
				message: __("Job Work In: goods will be received into {0}", [wh]),
				indicator: "blue",
			});
		}
		return;
	}

	if (frm.doc.entry_type === "Job Work Out" && is_job_work_receiver_gate_pass(frm) && ste) {
		const wh = resolve_job_work_dest_warehouse(gp, ste);
		if (wh) {
			frappe.show_alert({
				message: __("Job Work Out: goods will be received into {0}", [wh]),
				indicator: "blue",
			});
		}
		return;
	}

	if (frm.doc.entry_type === "Job Work Out" && receiver && ste && gp === ste) {
		const wh = resolve_job_work_dest_warehouse(receiver, ste);
		if (wh) {
			frappe.show_alert({
				message: __(
					"Job Work Out: after submit, create Job Work Out at {0} to receive into {1}",
					[receiver, wh]
				),
				indicator: "blue",
			});
		}
	}
}

function cache_referenced_stock_entry_type(frm) {
	if (
		frm.doc.document_reference !== STOCK_ENTRY_REFERENCE ||
		!frm.doc.reference_number
	) {
		frm._referenced_stock_entry_type = null;
		frm._referenced_ste_company = null;
		frm._referenced_ste_receiver = null;
		return Promise.resolve();
	}

	return frappe
		.call({
			method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.get_stock_entry_gate_pass_context",
			args: { stock_entry_name: frm.doc.reference_number },
		})
		.then((r) => {
			const row = r?.message || {};
			frm._referenced_stock_entry_type = row.stock_entry_type || null;
			frm._referenced_ste_company = row.company || null;
			frm._referenced_ste_receiver =
				row.receiver_company ||
				(row.party_type === "Company" ? row.party : null) ||
				null;
		})
		.catch(() => {
			frm._referenced_stock_entry_type = null;
			frm._referenced_ste_company = null;
			frm._referenced_ste_receiver = null;
		});
}

function suggest_stock_entry_entry_type(frm) {
	if (frm.doc.document_reference !== STOCK_ENTRY_REFERENCE) {
		return Promise.resolve();
	}
	if (!frm.doc.reference_number || !frm.doc.company) {
		return Promise.resolve();
	}
	return cache_referenced_stock_entry_type(frm).then(() => {
		const gp = (frm.doc.company || "").trim();
		const ste = (frm._referenced_ste_company || "").trim();
		const receiver = (frm._referenced_ste_receiver || "").trim();
		// Job work (Thusma ↔ JSB): always Job Work Out (sender or receiver).
		if (
			is_job_work_company_pair(gp, ste) ||
			is_job_work_company_pair(gp, receiver)
		) {
			return frm.set_value("entry_type", "Job Work Out");
		}
		if (gp && ste && gp !== ste) {
			if (INTERCOMPANY_WAREHOUSE_MAP[gp]) {
				return frm.set_value("entry_type", "Gate In");
			}
			return frm.set_value("entry_type", "Gate In");
		}
		return frm.set_value("entry_type", "Gate Out");
	});
}

function is_intercompany_material_transfer_gate_in(frm) {
	return (
		is_inbound_entry_type(frm.doc.entry_type) &&
		frm.doc.document_reference === STOCK_ENTRY_REFERENCE &&
		!!INTERCOMPANY_WAREHOUSE_MAP[frm.doc.company] &&
		(!frm._referenced_stock_entry_type ||
			frm._referenced_stock_entry_type === MATERIAL_TRANSFER_STOCK_ENTRY_TYPE)
	);
}

/**
 * Auto-fill item warehouses when the Gate Pass company is changed
 * to a company that has a mapped destination warehouse.
 */
function auto_set_intercompany_warehouses(frm) {
	if (!is_intercompany_material_transfer_gate_in(frm)) {
		return;
	}

	const company = frm.doc.company;
	if (!company) return;

	// Leave warehouse blank on the form for inter-company Gate In.
	// Setting a mapped name here can fail Link validation on save; Python
	// resolves the exact warehouse name on submit.
	const table = frm.doc.gate_pass_table || [];
	table.forEach((row) => {
		row.warehouse = "";
	});

	if (frm.gate_pass_ui) {
		frm.gate_pass_ui.load_items_from_table();
		(frm.gate_pass_ui.items || []).forEach((item) => {
			item.warehouse = "";
		});
		if (frm.gate_pass_ui.items.length) {
			frm.gate_pass_ui.sync_to_child_table();
		}
		frm.gate_pass_ui.render();
	} else if (table.length) {
		frm.refresh_field("gate_pass_table");
	}

	frappe.show_alert({
		message: __(
			"Destination warehouse will be set automatically on submit."
		),
		indicator: "blue",
	});
}

/**
 * Setup receipt creation buttons
 */
function setup_receipt_buttons(frm) {
	// Check if receipt already created
	const purchase_receipt_created = frm.doc.purchase_receipt;
	const subcontracting_receipt_created = frm.doc.subcontracting_receipt;

	// Show appropriate button based on document reference type
	if (frm.doc.document_reference === "Purchase Order") {
		if (!purchase_receipt_created) {
			frm.add_custom_button(__("Create Purchase Receipt"), function () {
				create_purchase_receipt(frm);
			}).addClass("btn-primary");
		} else {
			// Show link to created receipt
			frm.add_custom_button(__("View Purchase Receipt"), function () {
				frappe.set_route("Form", "Purchase Receipt", frm.doc.purchase_receipt);
			});
		}
	} else if (frm.doc.document_reference === "Subcontracting Order") {
		if (!subcontracting_receipt_created) {
			frm.add_custom_button(__("Create Subcontracting Receipt"), function () {
				create_subcontracting_receipt(frm);
			}).addClass("btn-primary");
		} else {
			// Show link to created receipt
			frm.add_custom_button(__("View Subcontracting Receipt"), function () {
				frappe.set_route("Form", "Subcontracting Receipt", frm.doc.subcontracting_receipt);
			});
		}
	}
}

/**
 * Create Purchase Receipt from Gate Pass
 */
function create_purchase_receipt(frm) {
	frappe.confirm(__("Create Purchase Receipt from this Gate Pass?"), function () {
		frappe.call({
			method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.create_purchase_receipt",
			args: {
				gate_pass_name: frm.doc.name,
			},
			freeze: true,
			freeze_message: __("Creating Purchase Receipt..."),
			callback: function (r) {
				if (r.message) {
					frappe.show_alert({
						message: __("Purchase Receipt {0} created successfully", [r.message]),
						indicator: "green",
					});
					// Redirect to the new Purchase Receipt
					frappe.set_route("Form", "Purchase Receipt", r.message);
				}
			},
		});
	});
}

/**
 * Create Subcontracting Receipt from Gate Pass
 */
function create_subcontracting_receipt(frm) {
	frappe.confirm(__("Create Subcontracting Receipt from this Gate Pass?"), function () {
		frappe.call({
			method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.create_subcontracting_receipt",
			args: {
				gate_pass_name: frm.doc.name,
			},
			freeze: true,
			freeze_message: __("Creating Subcontracting Receipt..."),
			callback: function (r) {
				if (r.message) {
					frappe.show_alert({
						message: __("Subcontracting Receipt {0} created successfully", [
							r.message,
						]),
						indicator: "green",
					});
					// Redirect to the new Subcontracting Receipt
					frappe.set_route("Form", "Subcontracting Receipt", r.message);
				}
			},
		});
	});
}

/**
 * Setup Stock Entry creation button for inbound gate passes
 */
async function setup_stock_entry_button(frm) {
	// Only show for inbound gate passes with Stock Entry reference
	if (frm.doc.document_reference === STOCK_ENTRY_REFERENCE && is_inbound_entry_type(frm.doc.entry_type)) {
		// Check if return_material_transfer exists and is valid
		// If the field is set, verify the document exists in the database
		// This handles cases where a draft Stock Entry was deleted
		const return_transfer = frm.doc.return_material_transfer;
		let has_valid_return_transfer = false;

		if (return_transfer) {
			try {
				has_valid_return_transfer = await frappe.db.exists("Stock Entry", return_transfer);
			} catch (e) {
				// Document doesn't exist or error checking
				has_valid_return_transfer = false;
			}
		}

		if (!has_valid_return_transfer) {
			// Show "Create Stock Entry" button if:
			// 1. return_material_transfer is not set, OR
			// 2. return_material_transfer is set but the document doesn't exist (was deleted)
			// But only if outbound_material_transfer exists (required to create return Stock Entry)
			if (frm.doc.outbound_material_transfer) {
				frm.add_custom_button(__("Create Stock Entry"), function () {
					create_stock_entry_from_gate_pass(frm);
				}).addClass("btn-primary");
			}
		} else {
			// Show link to created Stock Entry if it exists
			frm.add_custom_button(__("View Stock Entry"), function () {
				frappe.set_route("Form", "Stock Entry", return_transfer);
			});
		}
	}
}

/**
 * Create Stock Entry from inbound Gate Pass
 */
function create_stock_entry_from_gate_pass(frm) {
	frappe.confirm(
		__("Create a return Material Transfer Stock Entry from this inbound Gate Pass?"),
		function () {
			frappe.call({
				method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.create_stock_entry_from_inbound_gate_pass",
				args: {
					gate_pass_name: frm.doc.name,
				},
				freeze: true,
				freeze_message: __("Creating Stock Entry..."),
				callback: function (r) {
					if (r.message) {
						frappe.show_alert({
							message: __("Stock Entry {0} created successfully", [r.message]),
							indicator: "green",
						});
						// Reload the form to show the updated return_material_transfer field
						frm.reload_doc();
						// Redirect to the new Stock Entry
						frappe.set_route("Form", "Stock Entry", r.message);
					}
				},
			});
		}
	);
}

function load_reference_details(frm) {
	frappe.call({
		method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.get_reference_details",
		args: {
			document_reference: frm.doc.document_reference,
			reference_number: frm.doc.reference_number,
		},
		callback(response) {
			const details = response.message;
			console.log("Details: ", details);
			if (!details) {
				return;
			}

			const updates = {};

			if (details.company && !frm.doc.company) {
				updates.company = details.company;
			}

			if (details.address_display) {
				updates.address_display = details.address_display;
			}

			updates.e_invoice_status = details.e_invoice_status || null;
			updates.e_invoice_reference = details.e_invoice_reference || null;
			updates.e_waybill_status = details.e_waybill_status || null;
			updates.e_waybill_number = details.e_waybill_number || null;

			if (details.vehicle_number && !frm.doc.vehicle_number) {
				updates.vehicle_number = details.vehicle_number;
			}
			if (details.driver_name && !frm.doc.driver_name) {
				updates.driver_name = details.driver_name;
			}
			if (details.driver_contact && !frm.doc.driver_contact) {
				updates.driver_contact = details.driver_contact;
			}
			if (is_outbound_reference(frm.doc.document_reference, frm.doc.entry_type, frm)) {
				updates.supplier = null;
				updates.supplier_delivery_note = null;
			} else if (details.party_type === "Supplier" && details.party) {
				updates.supplier = details.party;
				if (details.supplier_delivery_note) {
					updates.supplier_delivery_note = details.supplier_delivery_note;
				}
			}
			console.log("Updates: ", updates);
			frm.set_value(updates).then(() => {
				frm.refresh();
				if (frm.doc.document_reference === "Stock Entry" && is_inbound_entry_type(frm.doc.entry_type)) {
					frappe.call({
						method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.get_origin_vehicle_details",
						args: { reference_number: frm.doc.reference_number },
						callback(r) {
							if (r.message && Object.keys(r.message).length > 0) {
								let origin_updates = {};
								if (!frm.doc.vehicle_number && r.message.vehicle_number) {
									origin_updates.vehicle_number = r.message.vehicle_number;
								}
								if (!frm.doc.driver_name && r.message.driver_name) {
									origin_updates.driver_name = r.message.driver_name;
								}
								if (!frm.doc.driver_contact && r.message.driver_contact) {
									origin_updates.driver_contact = r.message.driver_contact;
								}
								
								if (Object.keys(origin_updates).length > 0) {
									frm.set_value(origin_updates).then(() => {
										frm.set_value("fetched_vehicle_number", frm.doc.vehicle_number);
										frm.set_value("fetched_driver_name", frm.doc.driver_name);
									});
								} else {
									frm.set_value("fetched_vehicle_number", frm.doc.vehicle_number);
									frm.set_value("fetched_driver_name", frm.doc.driver_name);
								}
							}
						}
					});
				} else {
					frm.set_value("fetched_vehicle_number", frm.doc.vehicle_number);
					frm.set_value("fetched_driver_name", frm.doc.driver_name);
				}
			});
		},
	});
}

function load_reference_items(frm) {
	const fetchItems = () => {
		frappe.call({
			method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.get_items",
			args: {
				document_reference: frm.doc.document_reference,
				reference_number: frm.doc.reference_number,
				entry_type: frm.doc.entry_type,
				gp_company: frm.doc.company,
				exclude_gate_pass: frm.doc.name,
			},
			freeze: true,
			freeze_message: __("Loading items from reference document..."),
			callback(response) {
				const items = response.message || [];
				set_gate_pass_items(frm, items);
				show_job_work_warehouse_hint(frm);
			},
		});
	};

	if (frm.doc.document_reference === STOCK_ENTRY_REFERENCE) {
		cache_referenced_stock_entry_type(frm).then(fetchItems);
	} else {
		fetchItems();
	}
}

function set_gate_pass_items(frm, items) {
	frm.clear_table("gate_pass_table");
	// fetch the value of manual_return_flow
	const is_return_flow = parseInt(frm.doc.manual_return_flow || 0) === 1;
	const is_inbound = is_inbound_entry_type(frm.doc.entry_type);

	// For inter-company / job-work inbound Gate passes, pre-fill received_qty from STE qty.
	const is_intercompany_gate_in = is_intercompany_material_transfer_gate_in(frm);
	const is_job_work_receive =
		frm.doc.entry_type === "Job Work In" || is_job_work_receiver_gate_pass(frm);

	(items || []).forEach((item) => {
		const row = frm.add_child("gate_pass_table");
		row.item_code = item.item_code;
		row.item_name = item.item_name || "";
		row.batch_no = item.batch_no || "";
		row.description = item.description || "";
		row.uom = item.uom || "";
		row.stock_uom = item.stock_uom || "";
		row.conversion_factor = item.conversion_factor || 1.0;
		row.ordered_qty = item.ordered_qty || 0;
		// For inter-company Gate In pre-fill received_qty = ordered_qty
		// so guard sees what was dispatched; they can reduce if short-received.
		if (is_intercompany_gate_in || is_job_work_receive) {
			row.received_qty = item.ordered_qty || 0;
			row.dispatched_qty = 0;
		} else if (is_inbound) {
			row.received_qty = is_return_flow ? 0 : item.received_qty || item.ordered_qty || 0;
			row.dispatched_qty = 0;
		} else {
			// Gate Out
			row.received_qty = 0;
			row.dispatched_qty = item.dispatched_qty || item.ordered_qty || 0;
		}
		row.pending_qty = item.pending_qty || 0;
		row.is_rate_contract = item.is_rate_contract || 0;
		row.rate = item.rate || 0;
		const qty_for_amount = is_outbound_reference(
			frm.doc.document_reference,
			frm.doc.entry_type,
			frm
		)
			? row.dispatched_qty || 0
			: row.received_qty || 0;
		row.amount = qty_for_amount * (item.rate || 0);
		// For inter-company Gate In: clear warehouse (avoid Link field validation crash).
		// Guard can leave blank; Python on_submit fills from the inter-company warehouse map.
		// For same-company: use the warehouse from reference document.
		row.warehouse = is_intercompany_gate_in || is_job_work_receive ? "" : (item.warehouse || "");
		row.rejected_warehouse = item.rejected_warehouse || "";
		row.expense_account = item.expense_account || "";
		row.cost_center = item.cost_center || "";
		row.project = item.project || "";
		row.schedule_date = item.schedule_date || "";
		row.bom = item.bom || "";
		row.include_exploded_items = item.include_exploded_items || 0;
			row.order_item_name = item.order_item_name || "";
		row.item_group = item.item_group || "";
	});

	frm.refresh_field("gate_pass_table");

	if (frm.gate_pass_ui) {
		// Render immediately; refresh() is delayed and races with auto_set.
		frm.gate_pass_ui.load_items_from_table();
		frm.gate_pass_ui.render();
	}

	if (is_intercompany_gate_in) {
		auto_set_intercompany_warehouses(frm);
		frappe.show_alert({
			message: __(
				"Received Qty pre-filled from dispatched quantity. Adjust if any shortage."
			),
			indicator: "blue",
		});
	} else if (is_job_work_receive) {
		frappe.show_alert({
			message: __("Received Qty pre-filled. Warehouse set on submit from job-work rules."),
			indicator: "blue",
		});
	}
}

function toggle_discrepancy_fields(frm) {
	console.log(frm.doc.has_discrepancy);

	const show = frm.doc.has_discrepancy;
	const can_edit =
		(frm.perm && frm.perm[0] && frm.perm[0].write) ||
		frappe.perm.has_perm("Gate Pass", 0, "write");

	const fields = ["has_discrepancy", "lost_quantity", "damaged_quantity", "discrepancy_notes"];
	fields.forEach((fieldname) => {
		const read_only = !can_edit || frm.doc.docstatus > 0;
		frm.set_df_property(fieldname, "read_only", read_only);
	});

	frm.toggle_reqd("lost_quantity", show);
	frm.toggle_reqd("damaged_quantity", show);
}

function is_outbound_reference(documentReference, entryType, frm) {
	if (documentReference === STOCK_ENTRY_REFERENCE) {
		if (frm && is_job_work_receiver_gate_pass(frm)) {
			return false;
		}
		return is_outbound_entry_type(entryType);
	}
	return OUTBOUND_REFERENCES.includes(documentReference);
}

function toggle_stock_entry_link_permissions(frm) {
	const can_edit =
		(frm.perm && frm.perm[0] && frm.perm[0].write) ||
		frappe.perm.has_perm("Gate Pass", 0, "write");
	const read_only = !can_edit || frm.doc.docstatus > 0;

	["outbound_material_transfer", "return_material_transfer"].forEach((field) => {
		frm.set_df_property(field, "read_only", read_only);
	});
	frm.set_df_property("manual_return_flow", "read_only", read_only);
}

function refresh_compliance_status(frm) {
	const field = frm.fields_dict?.compliance_status_html;
	if (!field) {
		return;
	}

	if (
		!frm.doc.document_reference ||
		!frm.doc.reference_number ||
		!is_outbound_reference(frm.doc.document_reference, frm.doc.entry_type, frm)
	) {
		clear_compliance_status(frm);
		return;
	}

	frappe.call({
		method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.get_outbound_compliance_status",
		args: {
			document_reference: frm.doc.document_reference,
			reference_number: frm.doc.reference_number,
			gate_pass: frm.doc.name || null,
		},
		callback(response) {
			const status = response.message;
			set_compliance_status(field, status);
		},
	});
}

function clear_compliance_status(frm) {
	const field = frm.fields_dict?.compliance_status_html;
	if (!field) {
		return;
	}
	set_compliance_status(field, null);
}

function set_compliance_status(field, status) {
	const wrapper = field.$wrapper;
	if (!wrapper || wrapper.length === 0) {
		return;
	}
	if (!status) {
		wrapper.empty();
		return;
	}

	const level = status.level || "info";
	const title = frappe.utils.escape_html(status.title || "");
	const messages = Array.isArray(status.messages) ? status.messages : [];
	const description = status.description ? frappe.utils.escape_html(status.description) : "";

	let icon = "info-circle";
	if (level === "success") {
		icon = "check-circle";
	} else if (level === "warning") {
		icon = "exclamation-triangle";
	} else if (level === "error") {
		icon = "times-circle";
	}

	const body = [];
	if (title) {
		body.push(`<div class="compliance-banner-title">${title}</div>`);
	}

	if (description) {
		body.push(`<div class="compliance-banner-description">${description}</div>`);
	}

	if (messages.length) {
		const listItems = messages
			.map((message) => `<li>${frappe.utils.escape_html(message)}</li>`)
			.join("");
		body.push(`<ul class="compliance-banner-list">${listItems}</ul>`);
	}

	if (!body.length) {
		body.push(
			`<div class="compliance-banner-description">${__(
				"No compliance information available."
			)}</div>`
		);
	}

	const html = `
		<div class="compliance-banner compliance-${level}">
			<div class="compliance-banner-icon">
				<i class="fa fa-${icon}"></i>
			</div>
			<div class="compliance-banner-body">
				${body.join("")}
			</div>
		</div>
	`;

	wrapper.html(html);
}

function show_stock_entry_guidance(frm) {
	if (frm.doc.document_reference !== STOCK_ENTRY_REFERENCE) {
		return;
	}

	frm.dashboard.clear_comment();

	if (frm.doc.manual_return_flow) {
		frm.dashboard.add_comment(
			__(
				"Material is returning before a Stock Entry is recorded. Select the outbound transfer in the section below so quantities can be validated."
			),
			"yellow"
		);
	} else if (is_inbound_entry_type(frm.doc.entry_type) && !frm.doc.reference_number) {
		frm.dashboard.add_comment(
			__("Select the Stock Entry return document to link this Gate Pass."),
			"orange"
		);
	}
}

function process_qr_scan(frm, qr_text) {
	if (!qr_text) return;
	
	let data = {};
	qr_text.split('\n').forEach(line => {
		if (line.includes(':')) {
			let parts = line.split(':');
			let key = parts[0].trim().toLowerCase();
			let val = parts.slice(1).join(':').trim();
			data[key] = val;
		}
	});

	// Keys expected based on user format:
	// Company: Jayashree Spun Bond - 1ZT (origin, we ignore)
	// Challan No: MAT-STE-01546
	// DocType: Stock Entry
	// Date: 2026-05-21
	// Party: J Vasanth Exports
	
	let doctype = data["doctype"];
	if (!doctype) return frappe.msgprint(__("Invalid QR: DocType not found."));

	// Chain field updates so company/entry_type are set before reference_number loads items.
	frm.set_value("document_reference", doctype)
		.then(() => frm.set_value("company", data["party"] || frm.doc.company))
		.then(() => frm.set_value("entry_type", "Gate In"))
		.then(() => {
			if (data["challan no"]) {
				return frm.set_value("reference_number", data["challan no"]);
			}
		})
		.then(() => {
			frappe.show_alert({
				message: __("QR Code applied successfully"),
				indicator: "green",
			});
		});
}

/**
 * Modern warehouse picker for Purchase Order Gate In.
 * Company is read-only (PO / Gate Pass company); warehouses of that company only.
 * Uses Frappe Dialog + HTML (no external Vue CDN — blocked/blank on many sites).
 */
function show_po_location_picker(frm) {
	return new Promise((resolve) => {
		const company = (frm.doc.company || "").trim();
		if (!company) {
			frappe.msgprint(__("Set Company before selecting Location."));
			resolve(null);
			return;
		}

		frappe.call({
			method: "gate_entry.gate_entry.doctype.gate_pass.gate_pass.get_warehouses_for_company",
			args: { company },
			freeze: true,
			freeze_message: __("Loading warehouses..."),
			callback(r) {
				const warehouses = r.message || [];
				open_location_warehouse_dialog(company, warehouses, resolve);
			},
			error() {
				frappe.msgprint(__("Could not load warehouses for {0}", [company]));
				resolve(null);
			},
		});
	});
}

function open_location_warehouse_dialog(company, warehouses, resolve) {
	ensure_po_location_picker_styles();

	const d = new frappe.ui.Dialog({
		title: __("Select Location Warehouse"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "picker_host" }],
		primary_action_label: __("Confirm"),
		primary_action() {
			const selected = d._selected_warehouse;
			if (!selected) {
				frappe.msgprint(__("Please select a warehouse."));
				return;
			}
			d.hide();
			resolve(selected);
		},
		secondary_action_label: __("Cancel"),
		secondary_action() {
			d.hide();
			resolve(null);
		},
	});

	d.show();

	const $host = d.fields_dict.picker_host.$wrapper;
	$host.empty().append(`
		<div class="po-loc-picker">
			<div class="po-loc-company">
				<span class="po-loc-label">${frappe.utils.escape_html(__("Company"))}</span>
				<span class="po-loc-value">${frappe.utils.escape_html(company)}</span>
			</div>
			<input type="search" class="form-control po-loc-search"
				placeholder="${frappe.utils.escape_html(__("Search warehouse..."))}" />
			<div class="po-loc-list"></div>
		</div>
	`);

	const $list = $host.find(".po-loc-list");
	const $search = $host.find(".po-loc-search");

	const render_list = (query) => {
		const q = (query || "").toLowerCase().trim();
		const filtered = !q
			? warehouses
			: warehouses.filter(
					(w) =>
						(w.name || "").toLowerCase().includes(q) ||
						(w.warehouse_name || "").toLowerCase().includes(q)
			  );

		$list.empty();
		if (!filtered.length) {
			$list.append(
				`<div class="po-loc-empty">${frappe.utils.escape_html(
					warehouses.length
						? __("No warehouses match your search")
						: __("No warehouses found for this company. Create a warehouse first.")
				)}</div>`
			);
			return;
		}

		filtered.forEach((w) => {
			const name = w.name || "";
			const $row = $(`
				<button type="button" class="po-loc-row" data-warehouse="${frappe.utils.escape_html(name)}">
					<span class="po-loc-name">${frappe.utils.escape_html(name)}</span>
					${
						w.warehouse_name && w.warehouse_name !== name
							? `<span class="po-loc-sub">${frappe.utils.escape_html(w.warehouse_name)}</span>`
							: ""
					}
				</button>
			`);
			if (d._selected_warehouse === name) {
				$row.addClass("active");
			}
			$list.append($row);
		});
	};

	$list.on("click", ".po-loc-row", function () {
		const name = $(this).attr("data-warehouse");
		d._selected_warehouse = name;
		$list.find(".po-loc-row").removeClass("active");
		$(this).addClass("active");
	});

	$search.on("input", function () {
		render_list($(this).val());
	});

	render_list("");
	setTimeout(() => $search.trigger("focus"), 200);
}

function ensure_po_location_picker_styles() {
	if (document.getElementById("po-loc-picker-style")) {
		return;
	}
	const style = document.createElement("style");
	style.id = "po-loc-picker-style";
	style.textContent = `
		@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@500;600;700&display=swap');
		.po-loc-picker { font-family: "DM Sans", "Segoe UI", sans-serif; }
		.po-loc-company {
			display: flex; flex-direction: column; gap: 4px;
			padding: 14px 16px; margin-bottom: 14px;
			border-radius: 12px; background: linear-gradient(135deg, #f0f4f8 0%, #e8eef5 100%);
			border: 1px solid #dde3ea;
		}
		.po-loc-label {
			font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
			font-weight: 600; color: #5b6570;
		}
		.po-loc-value { font-size: 16px; font-weight: 700; color: #15202b; }
		.po-loc-search {
			margin-bottom: 12px; border-radius: 10px; height: 40px;
			border: 1px solid #cfd6dd; font-size: 14px;
		}
		.po-loc-list {
			max-height: 340px; overflow: auto; display: flex;
			flex-direction: column; gap: 8px; padding-right: 2px;
		}
		.po-loc-row {
			display: flex; flex-direction: column; gap: 2px; align-items: flex-start;
			text-align: left; border: 1px solid #e2e6ea; background: #fff;
			border-radius: 10px; padding: 12px 14px; cursor: pointer;
			transition: border-color .15s, background .15s, box-shadow .15s;
		}
		.po-loc-row:hover {
			border-color: #2490ef; box-shadow: 0 1px 4px rgba(36,144,239,.12);
		}
		.po-loc-row.active {
			border-color: #2490ef; background: #eef6ff;
			box-shadow: 0 0 0 1px #2490ef inset;
		}
		.po-loc-name { font-size: 14px; font-weight: 600; color: #15202b; }
		.po-loc-sub { font-size: 12px; color: #6c7680; }
		.po-loc-empty {
			padding: 28px 16px; text-align: center; color: #6c7680;
			font-weight: 500; border: 1px dashed #d0d7de; border-radius: 10px;
		}
	`;
	document.head.appendChild(style);
}
