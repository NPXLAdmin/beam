# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint

from beam.beam.scan.config import get_scan_doctypes


def setup_inventory_dimensions(inv_dim_dict_list: list[dict]) -> None:
	"""Create Inventory Dimensions and perform supporting setup.

	Each dictionary in the list should contain the following keys:
	  dimension_name      - required
	  reference_document  - required
	  target_fieldname    - optional
	  apply_to_all_doctypes - optional, defaults to 1
	  custom_carry_forward  - optional, defaults to 0

	Side-effects per dimension:
	  - Relabels ``Source {Name}`` custom fields to ``{Name}``.
	  - Hides / marks read-only ``Target {Name}`` custom fields (keeps Purchase Invoice Item
	    variant visible and relabeled).
	  - Sets ``no_copy`` on all generated custom fields and marks them read-only on doctypes that
	    aren't scannable form targets (based on BEAM's ``get_scan_doctypes``).
	"""
	frappe.flags.in_test = True
	for inv_dim_dict in inv_dim_dict_list:
		name = inv_dim_dict["dimension_name"]
		print(f"Setting up {name} Inventory Dimension")
		if frappe.db.exists("Inventory Dimension", name):
			continue

		inv_dim_dict.setdefault("apply_to_all_doctypes", 1)
		inv_dim = frappe.new_doc("Inventory Dimension")
		inv_dim.update(inv_dim_dict)
		inv_dim.save()

		for custom_field in frappe.get_all("Custom Field", {"label": f"Source {name}"}):
			frappe.set_value("Custom Field", custom_field.name, "label", name)

		for custom_field in frappe.get_all("Custom Field", {"label": f"Target {name}"}, ["name", "dt"]):
			if custom_field.dt == "Purchase Invoice Item":
				frappe.set_value("Custom Field", custom_field.name, "label", name)
			else:
				frappe.set_value("Custom Field", custom_field.name, "read_only", 1)
				frappe.set_value("Custom Field", custom_field["name"], "no_copy", 1)

		frm_doctypes = get_scan_doctypes()["frm"]

		for custom_field in frappe.get_all("Custom Field", {"label": name}, ["name", "dt"]):
			frappe.set_value("Custom Field", custom_field["name"], "no_copy", 1)

			if (
				custom_field["dt"] not in frm_doctypes
				and custom_field["dt"].replace(" Item", "").replace(" Detail", "") not in frm_doctypes
			):
				frappe.set_value("Custom Field", custom_field["name"], "read_only", 1)
				frappe.set_value("Custom Field", custom_field["name"], "no_copy", 1)


_CARRY_FORWARD_CACHE_KEY = "beam:inv_dim_carry_forward"


def get_carry_forward_dims() -> list[dict]:
	"""Cached list of Inventory Dimensions with carry_forward enabled. Cleared alongside
	the scan cache when an Inventory Dimension is updated or deleted."""

	def _fetch():
		fields = ["source_fieldname"]
		# custom_carry_forward_manufacture is synced with BEAM's customizations, after patches,
		# so the column can be missing mid-migrate. Select it only when present and default it
		# off, so submit-time carry-forward never depends on sync ordering.
		has_manufacture = frappe.db.has_column("Inventory Dimension", "custom_carry_forward_manufacture")
		if has_manufacture:
			fields.append("custom_carry_forward_manufacture")

		return [
			frappe._dict(
				{**d, "custom_carry_forward_manufacture": d.get("custom_carry_forward_manufacture", 0)}
			)
			for d in frappe.get_all(
				"Inventory Dimension",
				filters={"custom_carry_forward": 1},
				fields=fields,
			)
			if d.source_fieldname
		]

	return frappe.cache().get_value(_CARRY_FORWARD_CACHE_KEY, generator=_fetch)


def propagate_inventory_dimensions(doc, method=None):
	"""before_submit hook for Stock Entry: copy each carry-forward Inventory Dimension's source
	field into its transfer field (``to_<source_fieldname>``) on rows where that transfer field is
	empty. Only fires for dimensions flagged with `custom_carry_forward`, and only on rows that
	represent a real transfer (both s_warehouse and t_warehouse set, row has a stock item code).

	We write to ``to_<source_fieldname>`` -- the field ERPNext's stock controller reads for the
	incoming leg of a transfer -- rather than to ``target_fieldname``. ``target_fieldname`` names
	the Stock Ledger Entry column (which must stay ``<source_fieldname>`` so the SLE dimension keeps
	its expected name), so the two must not be conflated.

	App-specific flows that explicitly set the transfer field (including explicit `None`) are
	unaffected as long as their dimension is not flagged to carry forward."""
	dims = get_carry_forward_dims()
	if not dims:
		return

	for row in doc.items or []:
		if not row.item_code or not row.s_warehouse or not row.t_warehouse:
			continue
		for dim in dims:
			target = f"to_{dim.source_fieldname}"
			if row.get(dim.source_fieldname) and not row.get(target):
				row.set(target, row.get(dim.source_fieldname))


def propagate_manufacture_dimensions(doc, method=None):
	"""before_submit hook for Manufacture Stock Entries: carry a consumed raw material's dimension
	value forward onto the finished-good and scrap rows' source field.

	Only fires for dimensions flagged with `custom_carry_forward_manufacture` (a customer's
	shipping unit, say, but not Handling Unit: a finished good gets a brand-new Handling Unit),
	and only when consumption is not itemized (``Manufacturing Settings.material_consumption``
	off), where the entry has a single material receipt.

	The raw material's value reaches the finished good's Stock Ledger Entry through the source
	field: the finished-good row has only a target warehouse, so `propagate_inventory_dimensions`
	skips it and the stock controller posts the plain source field to the SLE."""
	if doc.doctype != "Stock Entry" or doc.purpose != "Manufacture":
		return

	if cint(frappe.db.get_single_value("Manufacturing Settings", "material_consumption")):
		return

	dims = [d for d in get_carry_forward_dims() if d.get("custom_carry_forward_manufacture")]
	for dim in dims:
		carry_raw_material_value_forward(doc, dim.source_fieldname)


def carry_raw_material_value_forward(doc, fieldname: str) -> None:
	"""Set the consumed raw materials' single value on every finished-good and scrap row that has none"""
	values = {
		row.get(fieldname)
		for row in doc.items or []
		if row.item_code and not row.is_finished_item and not row.is_scrap_item and row.get(fieldname)
	}
	if not values:
		return
	if len(values) > 1:
		label = frappe.get_meta("Stock Entry Detail").get_label(fieldname)
		frappe.throw(
			_(
				"Cannot carry {0} forward to the finished good: raw materials have differing "
				"values ({1}). Set {0} on the finished good manually."
			).format(label, ", ".join(sorted(values)))
		)
	value = values.pop()
	for row in doc.items or []:
		if row.item_code and (row.is_finished_item or row.is_scrap_item) and not row.get(fieldname):
			row.set(fieldname, value)
