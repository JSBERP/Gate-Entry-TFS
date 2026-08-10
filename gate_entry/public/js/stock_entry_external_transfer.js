/**
 * Gate Entry Stock Entry Enhancements
 *
 * Adds UX hints for Material Transfer transactions that require gate documentation.
 * The behaviour is delivered entirely via the gate_entry module so ERPNext core remains untouched.
 */

const GATE_ENTRY_MATERIAL_TRANSFER = "Material Transfer";

frappe.ui.form.on("Stock Entry", {
	onload(frm) {
		setup_external_transfer_behaviour(frm);
	},
	refresh(frm) {
		setup_external_transfer_behaviour(frm);
	},
	stock_entry_type(frm) {
		setup_external_transfer_behaviour(frm, { resetFlag: true });
	},
	ge_external_transfer(frm) {
		handle_external_transfer_toggle(frm);
	},
	is_return(frm) {
		// Update dashboard indicator when return flag changes
		const isMaterialTransfer = frm.doc.stock_entry_type === GATE_ENTRY_MATERIAL_TRANSFER;
		const externalTransferEnabled = cint(frm.doc.ge_external_transfer);
		update_dashboard_indicator(frm, isMaterialTransfer, externalTransferEnabled);
	},
});

/**
 * Primary setup routine that makes sure UI hints stay in sync with current state.
 *
 * @param {frappe.ui.Form} frm
 * @param {{ resetFlag?: boolean }} [options]
 */
function setup_external_transfer_behaviour(frm, options = {}) {
	if (!frm || frm.doc.doctype !== "Stock Entry") {
		return;
	}

	const isMaterialTransfer = frm.doc.stock_entry_type === GATE_ENTRY_MATERIAL_TRANSFER;
	const externalTransferEnabled = cint(frm.doc.ge_external_transfer);

	// Reset the checkbox if users switch away from Material Transfer
	if (!isMaterialTransfer && externalTransferEnabled) {
		frm.set_value("ge_external_transfer", 0);
	}

	// When the type changes back to Material Transfer ensure the user sees the helper note
	if (options.resetFlag && isMaterialTransfer) {
		show_external_transfer_prompt(frm, { onlyIfUnset: true });
	}

	update_dashboard_indicator(frm, isMaterialTransfer, externalTransferEnabled);
	toggle_instruction_placeholder(frm, externalTransferEnabled);
}

/**
 * Handle manual toggling of the External Transfer checkbox.
 *
 * @param {frappe.ui.Form} frm
 */
function handle_external_transfer_toggle(frm) {
	if (!frm || frm.doc.doctype !== "Stock Entry") {
		return;
	}

	const externalTransferEnabled = cint(frm.doc.ge_external_transfer) === 1;

	if (externalTransferEnabled) {
		show_external_transfer_prompt(frm);
		update_dashboard_indicator(frm, true, true);
		toggle_instruction_placeholder(frm, true);
	} else {
		update_dashboard_indicator(frm, true, false);
		toggle_instruction_placeholder(frm, false);
	}
}

/**
 * Display a toast and optional dialog nudging the user to review gate requirements.
 *
 * @param {frappe.ui.Form} frm
 * @param {{ onlyIfUnset?: boolean }} [options]
 */
function show_external_transfer_prompt(frm, options = {}) {
	const alreadyAcknowledged =
		(frm.doc.__onload && frm.doc.__onload.__ge_external_prompt_shown) ||
		frm.meta.__ge_external_prompt_shown;

	if (options.onlyIfUnset && alreadyAcknowledged) {
		return;
	}

	const instructionField = frm.get_field("ge_gate_pass_instruction");
	if (instructionField && !instructionField.df.description) {
		instructionField.df.description = __(
			"Share vehicle details or packaging notes for the gate team."
		);
		instructionField.refresh();
	}

	frappe.show_alert({
		message: __(
			"External movement flagged. A Gate Pass will be prepared when this entry is submitted."
		),
		indicator: "orange",
	});

	frm.meta.__ge_external_prompt_shown = true;
}

/**
 * Display a dashboard indicator to highlight external transfers and inbound returns.
 *
 * @param {frappe.ui.Form} frm
 * @param {boolean} isMaterialTransfer
 * @param {boolean} externalTransferEnabled
 */
function update_dashboard_indicator(frm, isMaterialTransfer, externalTransferEnabled) {
	if (!frm.dashboard) {
		return;
	}

	frm.dashboard.clear_headline();
	frm.dashboard.clear_comment();

	if (!isMaterialTransfer) {
		return;
	}

	const isReturn = cint(frm.doc.is_return) === 1;

	// For inbound material transfers (return entries), Gate Pass is always created
	if (isReturn) {
		// Show indicator with outbound transfer reference
		const indicatorLabel = __("Inbound Material Transfer: Return against Outbound Transfer");
		frm.dashboard.add_indicator(indicatorLabel, "green");

		// Try to get outbound transfer reference from doc_references table first
		let returnAgainst = null;
		if (frm.doc.doc_references && frm.doc.doc_references.length > 0) {
			// Look for Stock Entry reference in doc_references
			for (let ref of frm.doc.doc_references) {
				if (ref.link_doctype === "Stock Entry" && ref.link_name) {
					returnAgainst = ref.link_name;
					break;
				}
			}
		}

		// Function to show comment with gate pass status
		const showComment = function (outboundRef, gatePassStatus) {
			let message = "";
			const hasGatePass = gatePassStatus && gatePassStatus.exists;
			const isSubmitted = hasGatePass && gatePassStatus.docstatus === 1;

			if (outboundRef) {
				const outboundLink = frappe.utils.get_form_link("Stock Entry", outboundRef, true);
				if (isSubmitted) {
					// Gate Pass already created and submitted
					const gatePassLink = gatePassStatus.name
						? frappe.utils.get_form_link("Gate Pass", gatePassStatus.name, true)
						: "";
					message = __("This is a return transfer against outbound Stock Entry {0}.", [
						outboundLink,
					]);
					if (gatePassLink) {
						message += " " + __("Gate Pass {0} has been created.", [gatePassLink]);
					} else {
						message += " " + __("Gate Pass has been created.");
					}
				} else if (hasGatePass) {
					// Gate Pass created but not submitted
					const gatePassLink = frappe.utils.get_form_link(
						"Gate Pass",
						gatePassStatus.name,
						true
					);
					message = __(
						"This is a return transfer against outbound Stock Entry {0}. Gate Pass {1} has been created.",
						[outboundLink, gatePassLink]
					);
				} else {
					// No Gate Pass yet
					message = __(
						"This is a return transfer against outbound Stock Entry {0}. A Gate Pass will be created on submission.",
						[outboundLink]
					);
				}
			} else {
				if (isSubmitted) {
					// Gate Pass already created and submitted
					const gatePassLink = gatePassStatus.name
						? frappe.utils.get_form_link("Gate Pass", gatePassStatus.name, true)
						: "";
					message = __("This is an inbound material transfer.");
					if (gatePassLink) {
						message += " " + __("Gate Pass {0} has been created.", [gatePassLink]);
					} else {
						message += " " + __("Gate Pass has been created.");
					}
				} else if (hasGatePass) {
					// Gate Pass created but not submitted
					const gatePassLink = frappe.utils.get_form_link(
						"Gate Pass",
						gatePassStatus.name,
						true
					);
					message = __(
						"This is an inbound material transfer. Gate Pass {0} has been created.",
						[gatePassLink]
					);
				} else {
					// No Gate Pass yet
					message = __(
						"This is an inbound material transfer. A Gate Pass will be created on submission."
					);
				}
			}

			frm.dashboard.add_comment(message, "green");
		};

		// Check gate pass status if document is saved
		if (frm.doc.name) {
			// Function to get outbound ref and show comment
			const getOutboundAndShow = function (gatePassStatus) {
				if (!returnAgainst) {
					frappe.call({
						method: "gate_entry.stock_integration.utils.get_outbound_transfer_reference",
						args: {
							stock_entry_name: frm.doc.name,
						},
						callback: function (outboundResponse) {
							const outboundRef = outboundResponse.message;
							showComment(outboundRef, gatePassStatus);
						},
					});
				} else {
					showComment(returnAgainst, gatePassStatus);
				}
			};

			// First check if gate_pass field exists on the document
			if (frm.doc.gate_pass) {
				// Gate pass field exists, check if it's submitted
				frappe.db.get_value("Gate Pass", frm.doc.gate_pass, "docstatus", (r) => {
					const gatePassStatus = {
						exists: true,
						name: frm.doc.gate_pass,
						docstatus: r && r.docstatus !== undefined ? r.docstatus : null,
					};
					getOutboundAndShow(gatePassStatus);
				});
			} else {
				// No gate_pass field, check via server method
				frappe.call({
					method: "gate_entry.stock_integration.utils.get_gate_pass_status",
					args: {
						stock_entry_name: frm.doc.name,
					},
					callback: function (gatePassResponse) {
						const gatePassStatus = gatePassResponse.message;
						getOutboundAndShow(gatePassStatus);
					},
				});
			}
		} else if (returnAgainst) {
			// For new documents with doc_references, show message without gate pass check
			const outboundLink = frappe.utils.get_form_link("Stock Entry", returnAgainst, true);
			frm.dashboard.add_comment(
				__(
					"This is a return transfer against outbound Stock Entry {0}. A Gate Pass will be created on submission.",
					[outboundLink]
				),
				"green"
			);
		} else {
			// For new documents without doc_references, show generic message
			frm.dashboard.add_comment(
				__(
					"This is an inbound material transfer. A Gate Pass will be created on submission."
				),
				"green"
			);
		}
		return;
	}

	// For outbound material transfers, check external transfer flag
	const indicatorColor = externalTransferEnabled ? "orange" : "blue";
	const indicatorLabel = externalTransferEnabled
		? __("External Transfer: Gate Pass Required")
		: __("Internal Transfer: Gate Pass Not Required");

	frm.dashboard.add_indicator(indicatorLabel, indicatorColor);
}

/**
 * When external transfer is enabled, provide a placeholder to guide users.
 *
 * @param {frappe.ui.Form} frm
 * @param {boolean} externalTransferEnabled
 */
function toggle_instruction_placeholder(frm, externalTransferEnabled) {
	const field = frm.get_field("ge_gate_pass_instruction");
	if (!field) {
		return;
	}

	const htmlField = field.$wrapper ? field.$wrapper.find("textarea") : null;
	if (htmlField && htmlField.length) {
		const placeholderText = externalTransferEnabled
			? __("Notes for gate: e.g. vehicle no, driver, package count")
			: "";
		htmlField.attr("placeholder", placeholderText);
	}
}
