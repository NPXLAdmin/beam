# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Nested Handling Units. See docs/handling_unit_nesting.md.

Every test receives its own Handling Units, so none depends on another's bundles. Nest Cap is
raised per test and restored afterwards.
"""

import frappe
import pytest

from beam.beam.bundle import (
	count_bundles_exceeding,
	get_bundle_contents,
	get_bundle_entry_of,
	get_bundle_leaves,
	get_bundle_tree,
	get_container_of,
	is_container,
)
from beam.beam.scan import clear_inv_dim_cache, get_barcode_context, get_handling_unit

COMPANY = "Ambrosia Pie Company"
PIE = "Ambrosia Pie"
OTHER_PIE = "Gooseberry Pie"
BAKED_GOODS = "Baked Goods - APC"
KITCHEN = "Kitchen - APC"


@pytest.fixture
def nest_cap():
	original = frappe.db.get_value("BEAM Settings", COMPANY, "nest_cap")
	frappe.db.set_value("BEAM Settings", COMPANY, "nest_cap", 3)
	yield
	frappe.db.set_value("BEAM Settings", COMPANY, "nest_cap", original)


@pytest.fixture
def carry_forward():
	original = frappe.db.get_value("Inventory Dimension", "Handling Unit", "custom_carry_forward")
	frappe.db.set_value("Inventory Dimension", "Handling Unit", "custom_carry_forward", 1)
	clear_inv_dim_cache()
	yield
	frappe.db.set_value("Inventory Dimension", "Handling Unit", "custom_carry_forward", original or 0)
	clear_inv_dim_cache()


def receive(item=PIE, qty=10, warehouse=BAKED_GOODS) -> str:
	"""A Handling Unit holding stock, from a Material Receipt"""
	entry = stock_entry(
		"Material Receipt", [{"item_code": item, "qty": qty, "t_warehouse": warehouse}]
	)
	return entry.items[0].handling_unit


def stock_entry(purpose, rows, submit=True):
	entry = frappe.new_doc("Stock Entry")
	entry.stock_entry_type = entry.purpose = purpose
	entry.company = COMPANY
	for row in rows:
		rate = frappe.get_value("Item Price", {"item_code": row["item_code"]}, "price_list_rate") or 1
		entry.append("items", {"basic_rate": rate, "transfer_qty": row["qty"], **row})
	entry.save()
	if submit:
		entry.submit()
	return entry


def bundle_type(name, **policy) -> str:
	if frappe.db.exists("Handling Unit Bundle Type", name):
		doc = frappe.get_doc("Handling Unit Bundle Type", name)
	else:
		doc = frappe.new_doc("Handling Unit Bundle Type")
		doc.bundle_type_name = name
	defaults = {
		"allow_loose_handling_units": 1,
		"require_uniform_members": 0,
		"max_members": 0,
		"seal_when_contained": 0,
		"restrict_member_transactions": 0,
	}
	allowed = policy.pop("allowed_child_types", [])
	doc.update({**defaults, **policy})
	doc.set("allowed_child_types", [{"bundle_type": child} for child in allowed])
	doc.save()
	return doc.name


def pack(members, type_name="Box", handling_unit=None, purpose="Pack", submit=True):
	entry = frappe.new_doc("Handling Unit Bundle Entry")
	entry.update(
		{
			"company": COMPANY,
			"purpose": purpose,
			"bundle_type": type_name,
			"handling_unit": handling_unit,
		}
	)
	for member in members:
		entry.append("items", {"packed_doctype": "Handling Unit", "packed_handling_unit": member})
	entry.save()
	if submit:
		entry.submit()
	return entry


def scan_form(barcode, frm, doc=None):
	context = {"frm": frm, "doc": doc or {"doctype": frm, "company": COMPANY}}
	return frappe.call("beam.beam.scan.scan", barcode=barcode, context=context, current_qty=1)


# Bundle Type


@pytest.mark.order(300)
def test_bundle_type_cannot_hold_itself():
	bundle_type("Box")
	doc = frappe.get_doc("Handling Unit Bundle Type", "Box")
	doc.append("allowed_child_types", {"bundle_type": "Box"})
	with pytest.raises(frappe.ValidationError, match="cannot be listed among its own"):
		doc.save()


# Packing


@pytest.mark.order(301)
def test_new_container_is_minted_on_first_save(nest_cap):
	bundle_type("Box")
	leaf = receive()
	entry = pack([leaf], submit=False)
	assert entry.handling_unit
	assert frappe.db.exists(
		"Item Barcode", {"parent": entry.handling_unit, "barcode": entry.handling_unit}
	)
	assert get_barcode_context(entry.handling_unit).doc.name == entry.handling_unit


@pytest.mark.order(302)
def test_pack_records_contents_without_moving_stock(nest_cap):
	bundle_type("Box")
	leaves = [receive(), receive(OTHER_PIE)]
	sle_count = frappe.db.count("Stock Ledger Entry")
	entry = pack(leaves)
	assert frappe.db.count("Stock Ledger Entry") == sle_count
	assert get_bundle_contents(entry.handling_unit) == leaves
	assert get_container_of(leaves[0]) == entry.handling_unit
	assert get_bundle_entry_of(entry.handling_unit) == entry.name
	assert is_container([entry.handling_unit, leaves[0]]) == {entry.handling_unit}
	assert entry.warehouse == BAKED_GOODS
	assert {row.item_code for row in entry.items} == {PIE, OTHER_PIE}
	assert get_handling_unit(entry.handling_unit) is None


@pytest.mark.order(303)
def test_level_counts_a_drafts_own_rows(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet")
	box = pack([receive()])
	assert box.level == 2
	pallet = pack([box.handling_unit], type_name="Pallet", submit=False)
	assert pallet.level == 3
	empty = frappe.new_doc("Handling Unit Bundle Entry")
	empty.update({"company": COMPANY, "bundle_type": "Box"})
	assert empty.level is None


@pytest.mark.order(304)
def test_bundles_nest_and_leaves_resolve_at_any_depth(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet")
	leaves = [receive(), receive(), receive(OTHER_PIE)]
	box_a = pack(leaves[:2])
	box_b = pack(leaves[2:])
	pallet = pack([box_a.handling_unit, box_b.handling_unit], type_name="Pallet")
	assert pallet.level == 3
	assert sorted(get_bundle_leaves(pallet.handling_unit)) == sorted(leaves)
	assert pallet.items[0].packed_bundle_type == "Box"
	assert pallet.items[0].stock_qty == 20
	tree = get_bundle_tree(pallet.handling_unit)
	assert [node.value for node in tree] == [box_a.handling_unit, box_b.handling_unit]
	assert all(node.expandable for node in tree)
	assert not any(node.expandable for node in get_bundle_tree(box_a.handling_unit))


@pytest.mark.order(305)
def test_pack_by_bundle_entry_reference(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet")
	box = pack([receive()])
	pallet = frappe.new_doc("Handling Unit Bundle Entry")
	pallet.update({"company": COMPANY, "bundle_type": "Pallet"})
	pallet.append(
		"items", {"packed_doctype": "Handling Unit Bundle Entry", "packed_handling_unit": box.name}
	)
	pallet.save()
	pallet.submit()
	assert pallet.items[0].container_handling_unit == box.handling_unit
	assert get_container_of(box.handling_unit) == pallet.handling_unit


@pytest.mark.order(306)
def test_reusable_container_is_packed_again_after_unpacking(nest_cap):
	bundle_type("Tote")
	first, second = receive(), receive()
	tote = pack([first], type_name="Tote").handling_unit
	pack([first], type_name="Tote", handling_unit=tote, purpose="Unpack")
	pack([second], type_name="Tote", handling_unit=tote)
	assert get_bundle_contents(tote) == [second]
	assert get_container_of(first) is None


# Unpacking and correcting


@pytest.mark.order(307)
def test_unpack_removes_members(nest_cap):
	bundle_type("Box")
	leaves = [receive(), receive()]
	box = pack(leaves).handling_unit
	pack([leaves[0]], handling_unit=box, purpose="Unpack")
	assert get_bundle_contents(box) == [leaves[1]]
	assert get_container_of(leaves[0]) is None


@pytest.mark.order(308)
def test_cancel_reverts_contents_and_amend_supersedes(nest_cap):
	bundle_type("Box")
	leaves = [receive(), receive()]
	entry = pack(leaves)
	entry.cancel()
	assert get_bundle_contents(entry.handling_unit) == []
	amended = frappe.copy_doc(entry)
	amended.amended_from = entry.name
	amended.items = amended.items[:1]
	amended.insert()
	amended.submit()
	assert amended.handling_unit == entry.handling_unit
	assert get_bundle_contents(entry.handling_unit) == [leaves[0]]


# Nest Cap


@pytest.mark.order(309)
def test_nest_cap_of_one_disables_nesting():
	bundle_type("Box")
	with pytest.raises(frappe.ValidationError, match="Nest Cap"):
		pack([receive()])


@pytest.mark.order(310)
def test_nest_cap_is_measured_from_the_root(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet")
	box = pack([receive()])
	pack([box.handling_unit], type_name="Pallet")
	other_box = pack([receive()])
	# the box alone would be three deep, within the cap, but the pallet above it would be four
	with pytest.raises(frappe.ValidationError, match="4 levels deep"):
		pack([other_box.handling_unit], handling_unit=box.handling_unit)


@pytest.mark.order(311)
def test_lowering_nest_cap_below_existing_bundles_needs_acknowledging(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet")
	box = pack([receive()])
	pack([box.handling_unit], type_name="Pallet")
	assert count_bundles_exceeding(2, COMPANY)["count"] >= 1
	assert count_bundles_exceeding(2, COMPANY)["deepest"] >= 3
	settings = frappe.get_doc("BEAM Settings", COMPANY)
	settings.nest_cap = 2
	with pytest.raises(frappe.ValidationError, match="deeper than a Nest Cap of 2"):
		settings.save()
	# what the confirmation dialog does: the same save again, acknowledged
	settings = frappe.get_doc("BEAM Settings", COMPANY)
	settings.nest_cap = 2
	settings.acknowledge_deeper_bundles = 1
	settings.save()
	assert frappe.db.get_value("BEAM Settings", COMPANY, "acknowledge_deeper_bundles") == 0
	# grandfathered: the existing pallet still resolves to its leaves
	assert get_bundle_leaves(get_container_of(box.handling_unit))


# Nesting rules


@pytest.mark.order(312)
def test_loose_handling_units_can_be_refused(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet", allow_loose_handling_units=0)
	with pytest.raises(frappe.ValidationError, match="does not accept loose Handling Units"):
		pack([receive()], type_name="Pallet")
	box = pack([receive()])
	assert pack([box.handling_unit], type_name="Pallet").docstatus == 1


@pytest.mark.order(313)
def test_allowed_child_types(nest_cap):
	bundle_type("Box")
	bundle_type("Case")
	bundle_type("Pallet", allowed_child_types=["Case"])
	box = pack([receive()])
	with pytest.raises(frappe.ValidationError, match="a Box cannot go inside a Pallet"):
		pack([box.handling_unit], type_name="Pallet")


@pytest.mark.order(314)
def test_max_members_and_uniform_members_are_reported_together(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet", max_members=1, require_uniform_members=1)
	box = pack([receive()])
	with pytest.raises(frappe.ValidationError) as error:
		pack([box.handling_unit, receive()], type_name="Pallet")
	assert "holds at most 1" in str(error.value)
	assert "must hold one kind of thing" in str(error.value)


@pytest.mark.order(315)
def test_sealed_bundle_cannot_change_while_contained(nest_cap):
	bundle_type("Box", seal_when_contained=1)
	bundle_type("Pallet")
	box = pack([receive()])
	pack([box.handling_unit], type_name="Pallet")
	with pytest.raises(
		frappe.ValidationError, match="cannot be packed or unpacked while it is contained"
	):
		pack([receive()], handling_unit=box.handling_unit)
	bundle_type("Box")


@pytest.mark.order(316)
def test_nesting_policy_hook_can_add_violations(nest_cap, monkeypatch):
	bundle_type("Box")
	original = frappe.get_hooks

	def get_hooks(hook=None, *args, **kwargs):
		if hook == "beam_nesting_policy":
			return ["beam.tests.test_bundle.refuse_everything"]
		return original(hook, *args, **kwargs)

	monkeypatch.setattr(frappe, "get_hooks", get_hooks)
	with pytest.raises(frappe.ValidationError, match="Refused by policy"):
		pack([receive()])


def refuse_everything(entry, rows, violations):
	return [*violations, "Refused by policy"]


# Structural rules


@pytest.mark.order(317)
def test_contents_must_share_a_warehouse(nest_cap):
	bundle_type("Box")
	with pytest.raises(frappe.ValidationError, match="must be in one warehouse"):
		pack([receive(), receive(warehouse=KITCHEN)])


@pytest.mark.order(318)
def test_a_handling_unit_holding_stock_cannot_be_a_bundle(nest_cap):
	bundle_type("Box")
	with pytest.raises(frappe.ValidationError, match="holds stock of its own"):
		pack([receive()], handling_unit=receive())


@pytest.mark.order(319)
def test_a_unit_belongs_to_one_bundle_at_a_time(nest_cap):
	bundle_type("Box")
	leaf = receive()
	pack([leaf])
	with pytest.raises(frappe.ValidationError, match="already inside"):
		pack([leaf])


@pytest.mark.order(320)
def test_a_bundle_cannot_go_inside_itself(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet")
	box = pack([receive()])
	pallet = pack([box.handling_unit], type_name="Pallet")
	with pytest.raises(frappe.ValidationError, match="contains this bundle"):
		pack([pallet.handling_unit], handling_unit=box.handling_unit)


@pytest.mark.order(321)
def test_duplicate_rows_are_refused(nest_cap):
	bundle_type("Box")
	leaf = receive()
	with pytest.raises(frappe.ValidationError, match="already on row 1"):
		pack([leaf, leaf])


# Transactions


@pytest.mark.order(322)
def test_a_bundle_never_appears_on_a_transaction_row(nest_cap):
	bundle_type("Box")
	box = pack([receive()])
	with pytest.raises(frappe.ValidationError, match="is a bundle"):
		stock_entry(
			"Material Issue",
			[{"item_code": PIE, "qty": 1, "s_warehouse": BAKED_GOODS, "handling_unit": box.handling_unit}],
		)


@pytest.mark.order(323)
def test_restricted_bundle_refuses_member_transactions(nest_cap):
	bundle_type("Crate", restrict_member_transactions=1)
	leaf = receive()
	pack([leaf], type_name="Crate")
	with pytest.raises(frappe.ValidationError, match="does not allow its contents to be used"):
		stock_entry(
			"Material Issue",
			[{"item_code": PIE, "qty": 1, "s_warehouse": BAKED_GOODS, "handling_unit": leaf}],
		)


def issue_in_full(handling_unit):
	return stock_entry(
		"Material Issue",
		[{"item_code": PIE, "qty": 10, "s_warehouse": BAKED_GOODS, "handling_unit": handling_unit}],
	)


def system_unpack_for(voucher):
	return frappe.get_doc(
		"Handling Unit Bundle Entry", {"voucher_type": voucher.doctype, "voucher_no": voucher.name}
	)


@pytest.mark.order(324)
def test_depleting_a_member_unpacks_it(nest_cap):
	bundle_type("Box")
	used, kept = receive(), receive()
	box = pack([used, kept]).handling_unit
	issue = issue_in_full(used)
	assert get_bundle_contents(box) == [kept]
	unpack = system_unpack_for(issue)
	assert unpack.system_generated == 1
	assert unpack.purpose == "Unpack"
	assert [row.container_handling_unit for row in unpack.items] == [used]


@pytest.mark.order(325)
def test_cancel_that_recombines_returns_the_member(nest_cap):
	from beam.beam.overrides.stock_entry import set_rows_to_recombine

	bundle_type("Box")
	used, kept = receive(), receive()
	box = pack([used, kept]).handling_unit
	issue = issue_in_full(used)
	unpack = system_unpack_for(issue)
	set_rows_to_recombine(issue.name, [issue.items[0].name])
	issue.reload()
	issue.cancel()
	assert frappe.db.get_value("Handling Unit Bundle Entry", unpack.name, "docstatus") == 2
	assert get_bundle_contents(box) == [used, kept]


@pytest.mark.order(326)
def test_cancel_that_keeps_units_separate_leaves_the_member_out(nest_cap):
	bundle_type("Box")
	used, kept = receive(), receive()
	box = pack([used, kept]).handling_unit
	issue = issue_in_full(used)
	unpack = system_unpack_for(issue)
	issue.cancel()
	# without recombining, BEAM leaves the source Handling Unit consumed, so it is not back
	assert get_handling_unit(used) is None or get_handling_unit(used).stock_qty == 0
	assert frappe.db.get_value("Handling Unit Bundle Entry", unpack.name, "docstatus") == 1
	assert get_bundle_contents(box) == [kept]


@pytest.mark.order(327)
def test_partial_use_leaves_a_member_packed(nest_cap):
	bundle_type("Box")
	leaf = receive()
	box = pack([leaf]).handling_unit
	stock_entry(
		"Material Issue",
		[{"item_code": PIE, "qty": 4, "s_warehouse": BAKED_GOODS, "handling_unit": leaf}],
	)
	assert get_bundle_contents(box) == [leaf]


@pytest.mark.order(328)
def test_moving_one_member_away_unpacks_it(nest_cap, carry_forward):
	bundle_type("Box")
	moved, stays = receive(), receive()
	box = pack([moved, stays]).handling_unit
	stock_entry(
		"Material Transfer",
		[
			{
				"item_code": PIE,
				"qty": 10,
				"s_warehouse": BAKED_GOODS,
				"t_warehouse": KITCHEN,
				"handling_unit": moved,
			}
		],
	)
	assert get_bundle_contents(box) == [stays]


@pytest.mark.order(329)
def test_moving_a_whole_pallet_keeps_it_intact(nest_cap, carry_forward):
	bundle_type("Box")
	bundle_type("Pallet")
	leaves = [receive(), receive()]
	box = pack(leaves)
	pallet = pack([box.handling_unit], type_name="Pallet")
	stock_entry(
		"Material Transfer",
		[
			{
				"item_code": PIE,
				"qty": 10,
				"s_warehouse": BAKED_GOODS,
				"t_warehouse": KITCHEN,
				"handling_unit": leaf,
			}
			for leaf in leaves
		],
	)
	assert sorted(get_bundle_leaves(pallet.handling_unit)) == sorted(leaves)
	assert not frappe.db.exists(
		"Handling Unit Bundle Entry", {"system_generated": 1, "handling_unit": box.handling_unit}
	)


@pytest.mark.order(330)
def test_moving_a_whole_box_off_its_pallet_unpacks_the_box(nest_cap, carry_forward):
	bundle_type("Box")
	bundle_type("Pallet")
	moved_box = pack([receive(), receive()])
	other_box = pack([receive()])
	pallet = pack([moved_box.handling_unit, other_box.handling_unit], type_name="Pallet")
	stock_entry(
		"Material Transfer",
		[
			{
				"item_code": PIE,
				"qty": 10,
				"s_warehouse": BAKED_GOODS,
				"t_warehouse": KITCHEN,
				"handling_unit": row.container_handling_unit,
			}
			for row in moved_box.items
		],
	)
	assert get_bundle_contents(pallet.handling_unit) == [other_box.handling_unit]
	assert len(get_bundle_contents(moved_box.handling_unit)) == 2


# Scanning


@pytest.mark.order(331)
def test_scanning_a_bundle_adds_a_row_per_leaf(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet")
	leaves = [receive(), receive(OTHER_PIE)]
	box = pack(leaves)
	pallet = pack([box.handling_unit], type_name="Pallet")
	actions = scan_form(pallet.handling_unit, "Stock Reconciliation")
	assert sorted(action["context"]["handling_unit"] for action in actions) == sorted(leaves)
	assert {action["context"]["item_code"] for action in actions} == {PIE, OTHER_PIE}


@pytest.mark.order(332)
def test_scanning_a_bundle_in_a_list_filters_by_its_leaves(nest_cap):
	bundle_type("Box")
	leaves = [receive(), receive()]
	box = pack(leaves)
	actions = frappe.call(
		"beam.beam.scan.scan", barcode=box.handling_unit, context={"listview": "Stock Entry"}
	)
	assert actions[0]["operator"] == "in"
	assert actions[0]["doctype"] == "Stock Entry Detail"
	assert sorted(actions[0]["target"]) == sorted(leaves)


@pytest.mark.order(333)
def test_scanning_onto_a_bundle_entry_adds_a_packed_row(nest_cap):
	bundle_type("Box")
	leaf = receive()
	box = pack([receive()])
	leaf_action = scan_form(leaf, "Handling Unit Bundle Entry")[0]
	assert leaf_action["action"] == "add_packed_handling_unit"
	assert leaf_action["context"]["item_code"] == PIE
	assert leaf_action["context"]["packed_bundle_type"] is None
	box_action = scan_form(box.handling_unit, "Handling Unit Bundle Entry")[0]
	assert box_action["context"]["packed_bundle_type"] == "Box"
	assert box_action["context"]["stock_qty"] == 10


# Container identifiers and kits


@pytest.mark.order(334)
def test_container_identifier_is_issued_on_submit_and_scans(nest_cap, monkeypatch):
	bundle_type("Pallet")
	original = frappe.get_hooks

	def get_hooks(hook=None, *args, **kwargs):
		if hook == "beam_container_identifier":
			return {"Company": ["beam.tests.test_bundle.issue_identifier"]}
		return original(hook, *args, **kwargs)

	monkeypatch.setattr(frappe, "get_hooks", get_hooks)
	entry = pack([receive()], type_name="Pallet", submit=False)
	entry.identifier_scheme_doctype = "Company"
	entry.identifier_scheme = COMPANY
	entry.save()
	assert not entry.container_identifier
	entry.submit()
	assert entry.container_identifier == f"SSCC-{entry.name}"
	assert get_barcode_context(entry.container_identifier).doc.name == entry.handling_unit


def issue_identifier(scheme_doctype, scheme_name, entry):
	return f"SSCC-{entry.name}"


@pytest.mark.order(335)
def test_kit_completeness_counts_items_at_any_depth(nest_cap):
	bundle_type("Box")
	bundle_type("Pallet")
	kit = product_bundle({PIE: 20, OTHER_PIE: 10})
	box = pack([receive(), receive()])
	pallet = pack([box.handling_unit], type_name="Pallet", submit=False)
	pallet.product_bundle = kit
	pallet.save()
	assert pallet.kit_complete == 0
	pallet.append(
		"items", {"packed_doctype": "Handling Unit", "packed_handling_unit": receive(OTHER_PIE)}
	)
	pallet.save()
	assert pallet.kit_complete == 1


def product_bundle(contents: dict) -> str:
	parent = "Pie Sampler Kit"
	if not frappe.db.exists("Item", parent):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": parent,
				"item_group": frappe.db.get_value("Item", PIE, "item_group"),
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}
		).insert()
	if frappe.db.exists("Product Bundle", parent):
		frappe.delete_doc("Product Bundle", parent)
	bundle = frappe.new_doc("Product Bundle")
	bundle.new_item_code = parent
	for item_code, qty in contents.items():
		bundle.append("items", {"item_code": item_code, "qty": qty})
	bundle.insert()
	return bundle.name
