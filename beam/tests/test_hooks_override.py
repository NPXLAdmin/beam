# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from frappe import get_hooks


@pytest.fixture()
def patch_frappe_get_hooks(monkeymodule, *args, **kwargs):
	def patched_hooks(*args, **kwargs):
		hooks = get_hooks(*args, **kwargs)
		if "beam_frm" in args:
			return {
				"Item": {
					"Delivery Note": [
						{
							"action": "add_or_increment",
							"doctype": "Delivery Note Item",
							"field": "item_code",
							"target": "target.item_code",
						},
						{
							"action": "add_or_increment",
							"doctype": "Delivery Note Item",
							"field": "uom",
							"target": "target.uom",
						},
					]
				}
			}
		if "beam_listview" in args:
			return {
				"Item": {
					"Delivery Note": [
						{"action": "filter", "doctype": "Delivery Note Item", "field": "item_code"},
						{"action": "filter", "doctype": "Packed Item", "field": "item_code"},
					],
				}
			}
		return hooks

	monkeymodule.setattr("frappe.get_hooks", patched_hooks)


@pytest.mark.order(46)
def test_beam_frm_hooks_override(patch_frappe_get_hooks):
	item_barcode = frappe.get_value("Item Barcode", {"parent": "Kaduka Key Lime Pie"}, "barcode")
	dn = frappe.new_doc("Delivery Note")
	dn.customer = "Almacs Food Group"
	scan = frappe.call(
		"beam.beam.scan.scan",
		**{
			"barcode": str(item_barcode),
			"context": {"frm": dn.doctype, "doc": dn.as_dict()},
			"current_qty": 1,
		}
	)

	assert len(scan) == 2
	assert scan[0].get("action") == "add_or_increment"
	assert scan[0].get("doctype") == "Delivery Note Item"
	assert scan[0].get("field") == "item_code"
	assert scan[0].get("target") == "Kaduka Key Lime Pie"
	assert scan[1].get("action") == "add_or_increment"
	assert scan[1].get("doctype") == "Delivery Note Item"
	assert scan[1].get("field") == "uom"
	assert scan[1].get("target") == "Nos"


def build_warehouse_target(barcode_doc, context):
	"""A beam_frm_target builder for a doctype BEAM builds no target for on this form"""
	if barcode_doc.doc.doctype == "Warehouse":
		return frappe._dict(
			doctype="Warehouse", name=context.doc.get("name"), barcode_doc=barcode_doc.doc.name
		)


def add_item_group(barcode_doc, context, target):
	"""A beam_frm_target builder extending the target BEAM built"""
	if barcode_doc.doc.doctype == "Item" and target:
		target.item_group = frappe.get_cached_value("Item", barcode_doc.doc.name, "item_group")
		return target


@pytest.fixture()
def patch_frm_target_hooks(monkeypatch):
	def patched_hooks(*args, **kwargs):
		if "beam_frm" in args:
			return {
				"Warehouse": {"Delivery Note": [{"action": "set_scanned", "target": "target.barcode_doc"}]},
				"Item": {
					"Delivery Note": [
						{
							"action": "add_or_increment",
							"doctype": "Delivery Note Item",
							"field": "item_group",
							"target": "target.item_group",
						}
					]
				},
			}
		if "beam_frm_target" in args:
			return [build_warehouse_target, add_item_group]
		return get_hooks(*args, **kwargs)

	monkeypatch.setattr("frappe.get_hooks", patched_hooks)


def scan_delivery_note(barcode):
	dn = frappe.new_doc("Delivery Note")
	dn.name = "DN-SCAN-TARGET"
	return frappe.call(
		"beam.beam.scan.scan",
		barcode=str(barcode),
		context={"frm": dn.doctype, "doc": dn.as_dict()},
		current_qty=1,
	)


@pytest.mark.order(47)
def test_beam_frm_target_builds_a_target_beam_has_none_for(patch_frm_target_hooks):
	warehouse, barcode = frappe.get_value(
		"Item Barcode", {"parenttype": "Warehouse"}, ["parent", "barcode"]
	)
	scan = scan_delivery_note(barcode)
	assert scan[0]["target"] == warehouse
	assert scan[0]["context"]["name"] == "DN-SCAN-TARGET"


@pytest.mark.order(47)
def test_beam_frm_target_extends_the_target_beam_built(patch_frm_target_hooks):
	item_barcode = frappe.get_value("Item Barcode", {"parent": "Kaduka Key Lime Pie"}, "barcode")
	scan = scan_delivery_note(item_barcode)
	assert scan[0]["target"] == frappe.get_value("Item", "Kaduka Key Lime Pie", "item_group")
	assert scan[0]["context"]["item_code"] == "Kaduka Key Lime Pie"


@pytest.mark.order(48)
def test_beam_listview_hooks_override(patch_frappe_get_hooks):
	item_barcode = frappe.get_value("Item Barcode", {"parent": "Kaduka Key Lime Pie"}, "barcode")
	scan = frappe.call(
		"beam.beam.scan.scan",
		**{"barcode": str(item_barcode), "context": {"listview": "Delivery Note"}, "current_qty": 1}
	)

	assert len(scan) == 2
	assert scan[0].get("action") == "filter"
	assert scan[0].get("doctype") == "Delivery Note Item"
	assert scan[0].get("field") == "item_code"
	assert scan[0].get("target") == "Kaduka Key Lime Pie"
	assert scan[1].get("action") == "filter"
	assert scan[1].get("doctype") == "Packed Item"
	assert scan[1].get("field") == "item_code"
	assert scan[1].get("target") == "Kaduka Key Lime Pie"
