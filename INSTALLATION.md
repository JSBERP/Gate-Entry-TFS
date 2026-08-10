# Gate Entry Installation and Uninstallation Guide

This document describes the installation and uninstallation process for the Gate Entry app, including what customizations are made to ERPNext and how they are restored during uninstallation.

# Installation

### Prerequisites

- Frappe Framework installed
- ERPNext app installed
- India Compliance installed
- Bench CLI available

### Installation Steps

1. **Install the app using Bench:**

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app gate_entry --branch develop
bench install-app gate_entry
```

2. **The installation process automatically:**

   - Creates custom fields on ERPNext doctypes (via `after_install` hook)
   - Registers document event handlers for Stock Entry, Purchase Receipt, and Subcontracting Receipt
   - Sets up Security Guard role permissions
   - Creates required reports (Gate Register, Material Reconciliation, Pending Gate Passes)
   - Reloads Gate Pass DocType metadata

3. **Clear cache and restart:**

```bash
bench clear-cache
bench restart
```

### Manual Custom Fields Setup (if needed)

If custom fields were not created during installation (e.g., ERPNext was not installed yet), run:

```bash
bench execute gate_entry.setup.setup_custom_fields.setup
```

Or from Frappe console:

```python
from gate_entry.setup.setup_custom_fields import setup
setup()
```

## What Gets Added to ERPNext

### Custom Fields

The app adds the following custom fields to ERPNext doctypes **without modifying core files**:

#### Stock Entry

1. **`ge_external_transfer`** (Check)
   - Label: External Transfer
   - Location: After `stock_entry_type` field
   - Visible when: `stock_entry_type == "Material Transfer"`
   - Purpose: Flags material transfers that leave the plant and require a Gate Pass
   - Default: 0 (unchecked)

2. **`ge_gate_pass_instruction`** (Small Text)
   - Label: Gate Pass Instructions
   - Location: After `ge_external_transfer` field
   - Visible when: `ge_external_transfer == 1`
   - Purpose: Optional notes for the gate/security team

3. **`gate_pass`** (Link)
   - Label: Gate Pass
   - Location: After `inspection_required` field
   - Options: Gate Pass
   - Read-only: Yes
   - Purpose: Links to the Gate Pass created for this Stock Entry

#### Purchase Receipt

1. **`gate_pass`** (Link)
   - Label: Gate Pass
   - Location: After `supplier_delivery_note` field
   - Options: Gate Pass
   - Read-only: Yes
   - Purpose: Links to the Gate Pass used to create this Purchase Receipt

#### Subcontracting Receipt

1. **`gate_pass`** (Link)
   - Label: Gate Pass
   - Location: After `supplier_delivery_note` field
   - Options: Gate Pass
   - Read-only: Yes
   - Purpose: Links to the Gate Pass used to create this Subcontracting Receipt

### Document Event Handlers

The app registers the following document event handlers in `hooks.py`:

#### Stock Entry Events

- **`on_submit`**: Auto-creates Gate Pass for external Material Transfers and Send to Subcontractor entries
- **`on_cancel`**: Clears Gate Pass references and cancels auto-created gate passes
- **`on_trash`**: Clears Gate Pass references when Stock Entry is deleted

#### Purchase Receipt Events

- **`on_cancel`**: Clears `gate_pass` reference from Gate Pass
- **`on_trash`**: Clears `gate_pass` reference from Gate Pass

#### Subcontracting Receipt Events

- **`on_cancel`**: Clears `gate_pass` reference from Gate Pass
- **`on_trash`**: Clears `gate_pass` reference from Gate Pass

### Document Links

Bidirectional document links are configured to show relationships in the Connections sidebar:

- Gate Pass ↔ Purchase Receipt
- Gate Pass ↔ Subcontracting Receipt
- Gate Pass ↔ Stock Entry

### Client Scripts

- **`gate_pass_custom_ui.js`**: Custom UI logic for Gate Pass forms
- **`stock_entry_external_transfer.js`**: UI logic for Stock Entry External Transfer checkbox

### Reports

- **Gate Register**: Report showing all gate pass entries
- **Material Reconciliation**: Report for reconciling material movements
- **Pending Gate Passes**: Report showing pending gate pass operations

### Permissions

- **Security Guard Role**: Read-only access to Sales Invoice, Delivery Note, and GST Settings
- Desk access disabled for Security Guard role

## Uninstallation

### Uninstallation Steps

1. **Uninstall the app:**

```bash
bench uninstall-app gate_entry
```

2. **The uninstallation process automatically:**

   - Deletes all custom fields from ERPNext doctypes (via `before_uninstall` hook)
   - Clears cached metadata
   - Removes document event handlers
   - Removes document links configuration

3. **Clear cache and restart:**

```bash
bench clear-cache
bench restart
```

### What Gets Removed

#### Custom Fields Deleted

The `before_uninstall` hook removes all custom fields added by the app:

- **Stock Entry**: `ge_external_transfer`, `ge_gate_pass_instruction`, `gate_pass`
- **Purchase Receipt**: `gate_pass`
- **Subcontracting Receipt**: `gate_pass`

#### Event Handlers Removed

All document event handlers registered in `hooks.py` are automatically removed when the app is uninstalled.

#### Document Links Removed

All document link configurations are removed.

### Verification After Uninstallation

To verify that ERPNext has been restored to its original state:

1. **Check Custom Fields:**

```bash
bench console
```

```python
import frappe

# Check Stock Entry custom fields
stock_entry_fields = frappe.get_all(
    "Custom Field",
    filters={"dt": "Stock Entry", "fieldname": ["in", ["ge_external_transfer", "ge_gate_pass_instruction", "gate_pass"]]}
)
print(f"Stock Entry custom fields remaining: {len(stock_entry_fields)}")  # Should be 0

# Check Purchase Receipt custom fields
pr_fields = frappe.get_all(
    "Custom Field",
    filters={"dt": "Purchase Receipt", "fieldname": "gate_pass"}
)
print(f"Purchase Receipt custom fields remaining: {len(pr_fields)}")  # Should be 0

# Check Subcontracting Receipt custom fields
sr_fields = frappe.get_all(
    "Custom Field",
    filters={"dt": "Subcontracting Receipt", "fieldname": "gate_pass"}
)
print(f"Subcontracting Receipt custom fields remaining: {len(sr_fields)}")  # Should be 0
```

2. **Check DocType Existence:**

```python
# Gate Pass DocType should not exist
gate_pass_exists = frappe.db.exists("DocType", "Gate Pass")
print(f"Gate Pass DocType exists: {gate_pass_exists}")  # Should be False
```

3. **Verify Stock Entry Form:**

- Open a Stock Entry form
- Verify that `ge_external_transfer`, `ge_gate_pass_instruction`, and `gate_pass` fields are not visible
- The form should match the original ERPNext Stock Entry form

## Important Notes

### Data Preservation

- **Gate Pass Data**: All Gate Pass documents and related data are deleted when the app is uninstalled. This is expected behavior as Gate Pass is a custom DocType provided by this app.

- **Stock Entry Data**: Stock Entry documents are preserved, but the custom fields (`ge_external_transfer`, `ge_gate_pass_instruction`, `gate_pass`) are removed. Any values stored in these fields will be lost.

- **Purchase Receipt / Subcontracting Receipt Data**: Receipt documents are preserved, but the `gate_pass` field is removed. Any links to Gate Pass documents will be lost.

### Re-installation

If you reinstall the app after uninstallation:

1. Custom fields will be recreated
2. Event handlers will be re-registered
3. Document links will be restored
4. However, any Gate Pass data that was deleted during uninstallation will not be restored

### Manual Cleanup (if needed)

If automatic cleanup fails during uninstallation, you can manually delete custom fields:

```bash
bench console
```

```python
import frappe

custom_fields_to_delete = [
    "Stock Entry-ge_external_transfer",
    "Stock Entry-ge_gate_pass_instruction",
    "Stock Entry-gate_pass",
    "Purchase Receipt-gate_pass",
    "Subcontracting Receipt-gate_pass",
]

for field_name in custom_fields_to_delete:
    if frappe.db.exists("Custom Field", field_name):
        frappe.delete_doc("Custom Field", field_name, force=1, ignore_permissions=True)
        print(f"Deleted: {field_name}")

frappe.db.commit()
```

## Troubleshooting

### Custom Fields Not Created

**Symptom**: Custom fields are missing after installation.

**Solution**: Run manual setup:

```bash
bench execute gate_entry.setup.setup_custom_fields.setup
bench clear-cache
bench restart
```

### Custom Fields Not Deleted

**Symptom**: Custom fields remain after uninstallation.

**Solution**: Use manual cleanup (see above) or check Error Log for uninstallation errors.

### Gate Pass Not Auto-Created

**Symptom**: Gate Pass is not created when Stock Entry is submitted.

**Checklist**:
1. Verify `ge_external_transfer` checkbox is checked
2. Verify Stock Entry type is "Material Transfer" or "Send to Subcontractor"
3. Check Error Log for background job failures
4. Verify hooks are registered: `bench console` → `frappe.get_hooks("doc_events")`

### Stock Entry Cannot Be Cancelled

**Symptom**: Stock Entry with linked Gate Pass cannot be cancelled.

**Solution**: This is expected behavior. Cancel the Gate Pass first, then cancel the Stock Entry. The event handlers will automatically clear references.

## Testing Uninstallation

To test that uninstallation properly restores ERPNext:

1. **Before Uninstallation:**
   - Note the number of custom fields on Stock Entry, Purchase Receipt, Subcontracting Receipt
   - Create a test Stock Entry with `ge_external_transfer` checked
   - Verify the field is visible

2. **Uninstall the app**

3. **After Uninstallation:**
   - Verify custom fields are removed
   - Verify Stock Entry form matches original ERPNext form
   - Verify no Gate Pass DocType exists
   - Verify no errors in Error Log

## Summary

The Gate Entry app is designed to be **completely removable** without leaving traces in ERPNext core. All customizations are:

- Added via custom fields (not core file modifications)
- Registered via hooks (not core file modifications)
- Removed automatically during uninstallation
- Verified to restore ERPNext to its original state

The app follows Frappe best practices for app development, ensuring that ERPNext core remains untouched and can be restored to its original state after uninstallation.

