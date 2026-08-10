"""Shared constants for the Gate Entry module."""

from __future__ import annotations

INBOUND_REFERENCES = frozenset({"Purchase Order", "Subcontracting Order"})
OUTBOUND_REFERENCES = frozenset({"Sales Invoice", "Delivery Note"})
ALL_REFERENCES = INBOUND_REFERENCES | OUTBOUND_REFERENCES | {"Stock Entry"}

REFERENCE_PARTY_FIELDS = {
	"Purchase Order": ("supplier", "supplier_name"),
	"Subcontracting Order": ("supplier", "supplier_name"),
	"Sales Invoice": ("customer", "customer_name"),
	"Delivery Note": ("customer", "customer_name"),
}

REFERENCE_TOTAL_FIELDS = (
	"rounded_total",
	"grand_total",
	"base_grand_total",
	"net_total",
	"total",
	"base_total",
)

# Inter-company Gate In (Stock Entry, Material Transfer): receiving company → destination warehouse.
# Keep gate_pass.js INTERCOMPANY_WAREHOUSE_MAP in sync.
INTERCOMPANY_MATERIAL_TRANSFER_DEST_WAREHOUSE_MAP = {
	"J Vasanth Exports": "Finished Goods Warehouse - JVE",
	"Thusma SMS Nonwovens Private Limited - 1Z0": "Finished Goods Warehouse - TSNPL",
	"Jayashree Spun Bond - 2ZS": "Finished Goods - JSB-2ZS",
	"Thusma SMS Nonwoven Private Limited - 2ZZ": "Finished Goods Warehouse - TSNPL-2ZZ",
	"Varshine Tex (Odisha)": "Finished Goods Warehouse - VTO",
	"Thusma T Tex": "Finished Goods Warehouse - TTT",
	"Varshine Retails Private Limited": "Finished Goods Warehouse - VRPL",
	"Varshine Tex (Puducherry)": "Raw Materials Warehouse  - VTP",
}

MATERIAL_TRANSFER_STOCK_ENTRY_TYPE = "Material Transfer"

THUSMA_JOB_WORK_COMPANY = "Thusma SMS Nonwovens Private Limited - 1Z0"
JSB_JOB_WORK_COMPANIES = frozenset(
	{
		"Jayashree Spun Bond - 1ZT",
		"Jayashree Spun Bond - 2ZS",
	}
)

# Job-work destination warehouse: (receiving GP company, STE sender company) → warehouse.
# Job-work destination warehouse: (receiving GP company, STE sender company) → warehouse.
# Both sender and receiver use Job Work Out; receiver submit stores into mapped warehouse.
# Flow: JSB→Thusma RM — Job Work Out @ JSB (auto), Job Work Out @ Thusma (receive).
#       Thusma→JSB FG — Job Work Out @ Thusma (auto), Job Work Out @ JSB (receive).
# Keep gate_pass.js JOB_WORK_DEST_WAREHOUSE_MAP in sync.
JOB_WORK_DEST_WAREHOUSE_MAP = {
	# RM received at Thusma — JSB sent job work RM
	(THUSMA_JOB_WORK_COMPANY, "Jayashree Spun Bond - 1ZT"): "Jayashree 1ZT - JWO RM - TSNPL",
	(THUSMA_JOB_WORK_COMPANY, "Jayashree Spun Bond - 2ZS"): "Jayashree 2ZS - JWO RM - TSNPL",
	# FG received at JSB — Thusma sent finished goods back
	("Jayashree Spun Bond - 1ZT", THUSMA_JOB_WORK_COMPANY): "Thusma 1Z0 - JWI FG - JSB-1ZT",
	("Jayashree Spun Bond - 2ZS", THUSMA_JOB_WORK_COMPANY): "Thusma 1Z0 - JWI FG - JSB-2ZS",
}

# Backwards-compatible alias
JOB_WORK_IN_DEST_WAREHOUSE_MAP = JOB_WORK_DEST_WAREHOUSE_MAP

INBOUND_STOCK_ENTRY_ENTRY_TYPES = frozenset({"Gate In", "Job Work In"})
OUTBOUND_STOCK_ENTRY_ENTRY_TYPES = frozenset({"Gate Out", "Job Work Out"})
