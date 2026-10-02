# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import comma_and, flt

from beam.beam.bundle import (
	depth_below,
	evaluate_nesting_policy,
	get_ancestors,
	get_bundle_contents,
	get_containers_of,
	get_contents_tree,
	get_leaf_balances,
	get_nest_cap,
	get_packed_rows_details,
	walk,
)

"""
See docs/handling_unit_nesting.md
"""


class HandlingUnitBundleEntry(Document):
	# cancelling an entry packed by name inside another must not be blocked by that link: the
	# fold excludes cancelled entries, so cancelling is the correction and there is nothing to undo
	ignore_linked_doctypes = ("Handling Unit Bundle Entry",)

	def before_save(self):
		self.create_bundle_handling_unit()

	def validate(self):
		self.resolve_packed_rows()
		self.validate_no_cycle()
		self.validate_nest_cap()
		self.validate_nesting_rules()
		self.validate_warehouse_consistency()
		self.validate_bundle_holds_no_stock()
		self.set_kit_complete()

	def before_submit(self):
		# an entry inserted directly as submitted never runs before_save
		self.create_bundle_handling_unit()

	def on_submit(self):
		self.validate_single_membership()
		self.issue_container_identifier()

	@property
	def level(self):
		"""
		Depth of the bundle below its own Handling Unit, counting a draft's rows as though it
		were submitted. Blank while there is nothing inside, since an empty bundle has no depth.
		"""
		members = self.get_projected_members() if self.docstatus == 0 else self.get_current_members()
		if not members:
			return None
		return 1 + max(node.depth for node in walk(members))

	def create_bundle_handling_unit(self):
		"""
		A new container gets its Handling Unit on first save. The Handling Unit controller creates
		its barcode, so the container can be labelled and scanned before anything is packed.
		"""
		if self.handling_unit:
			return
		handling_unit = frappe.new_doc("Handling Unit")
		handling_unit.save()
		self.handling_unit = handling_unit.name

	def resolve_packed_rows(self):
		"""Resolve each row to the Handling Unit it names and fill its display values"""
		for row in self.items:
			row.container_handling_unit = self.get_row_handling_unit(row)
		details = get_packed_rows_details([row.container_handling_unit for row in self.items])
		for row in self.items:
			row.update(details.get(row.container_handling_unit, {}))
		self.validate_no_duplicate_rows()

	def get_row_handling_unit(self, row) -> str | None:
		if row.packed_doctype == "Handling Unit Bundle Entry":
			return frappe.db.get_value(
				"Handling Unit Bundle Entry", row.packed_handling_unit, "handling_unit"
			)
		return row.packed_handling_unit

	def validate_no_duplicate_rows(self):
		seen = {}
		for row in self.items:
			if row.container_handling_unit in seen:
				frappe.throw(
					_("Row {0}: {1} is already on row {2}.").format(
						row.idx, row.container_handling_unit, seen[row.container_handling_unit]
					),
					title=_("Duplicate Row"),
				)
			seen[row.container_handling_unit] = row.idx

	def validate_no_cycle(self):
		"""
		Single membership does not prevent a cycle: pack A into B, then B into A, and neither
		check fails because B is a bundle, not a member. Reject the bundle itself or anything above
		it, so depth can never recurse forever.
		"""
		if not self.handling_unit:
			return
		forbidden = {self.handling_unit, *get_ancestors(self.handling_unit, self.name)}
		for row in self.items:
			if row.container_handling_unit in forbidden:
				frappe.throw(
					_(
						"Row {0}: {1} already contains this bundle, so packing it here would create a loop."
					).format(row.idx, row.container_handling_unit),
					title=_("Bundle Inside Itself"),
				)

	def validate_nest_cap(self):
		"""
		The cap applies to the whole arrangement, measured from its root, because packing two
		levels down can push it past the cap and a sibling branch may already be deeper.
		"""
		if self.purpose != "Pack" or not self.items:
			return
		nest_cap = get_nest_cap(self.company)
		depth = self.get_projected_arrangement_depth()
		if depth > nest_cap:
			frappe.throw(
				_(
					"This would make the arrangement {0} levels deep, but Nest Cap for {1} is {2}. Depth is measured across the whole arrangement, not this bundle alone."
				).format(depth, self.company, nest_cap),
				title=_("Nest Cap Exceeded"),
			)

	def get_projected_arrangement_depth(self) -> int:
		members = self.get_projected_members()
		if not self.handling_unit:
			return 1 + max(node.depth for node in walk(members))
		ancestors = get_ancestors(self.handling_unit, self.name)
		root = ancestors[-1] if ancestors else self.handling_unit
		return depth_below(root, {self.handling_unit: members})

	def validate_nesting_rules(self):
		# a system-generated Unpack corrects the ledger, and must succeed even where the type
		# forbids modifying the bundle by hand
		if self.system_generated:
			return
		violations = evaluate_nesting_policy(self, self.items)
		if violations:
			frappe.throw("<br>".join(violations), title=_("Nesting Rules Not Met"))

	def validate_warehouse_consistency(self):
		"""
		Everything inside a bundle is in one warehouse. Only a Pack can break that; an Unpack
		takes things out, so it records where the remainder is without refusing.
		"""
		warehouses = {
			balance.warehouse for balance in self.get_projected_leaf_balances() if balance.warehouse
		}
		self.warehouse = next(iter(warehouses)) if len(warehouses) == 1 else None
		if self.purpose == "Pack" and len(warehouses) > 1:
			frappe.throw(
				_("A bundle cannot straddle warehouses. This entry mixes {0}.").format(
					comma_and(sorted(warehouses), add_quotes=False)
				),
				title=_("Contents in More Than One Warehouse"),
			)

	def validate_bundle_holds_no_stock(self):
		"""A Handling Unit either holds stock or holds other Handling Units; never both"""
		if not self.handling_unit:
			return
		if frappe.db.exists(
			"Stock Ledger Entry", {"handling_unit": self.handling_unit, "is_cancelled": 0}
		):
			frappe.throw(
				_(
					"Handling Unit {0} holds stock of its own, so it cannot be used as a bundle. A Handling Unit either holds stock or holds other Handling Units."
				).format(self.handling_unit),
				title=_("Bundle Holds Stock"),
			)

	def set_kit_complete(self):
		"""
		Compared against everything inside at any depth, summed per item, so a pallet of boxes is
		judged on the items in the boxes. Reported, not enforced.
		"""
		if not self.product_bundle:
			self.kit_complete = 0
			return
		held = defaultdict(float)
		for balance in self.get_projected_leaf_balances():
			held[balance.item_code] += flt(balance.qty)
		required = frappe.get_all(
			"Product Bundle Item", filters={"parent": self.product_bundle}, fields=["item_code", "qty"]
		)
		self.kit_complete = int(all(flt(held[row.item_code]) == flt(row.qty) for row in required))

	def validate_single_membership(self):
		"""
		Anything packed must not already be inside a bundle. Checked at submit rather than on
		save, so a draft can be built up without racing another one.
		"""
		if self.purpose != "Pack":
			return
		containers = get_containers_of([row.container_handling_unit for row in self.items], self.name)
		for row in self.items:
			if row.container_handling_unit in containers:
				frappe.throw(
					_("Row {0}: {1} is already inside {2}. Unpack it first.").format(
						row.idx, row.container_handling_unit, containers[row.container_handling_unit]
					),
					title=_("Already Packed"),
				)

	def issue_container_identifier(self):
		"""
		Ask the application that owns the identifier scheme for the next identifier, and give the
		container a barcode for it so the printed string scans back to the bundle
		"""
		if not self.identifier_scheme or self.container_identifier:
			return
		# an unregistered hook comes back as an empty list, not a dict
		providers = frappe.get_hooks("beam_container_identifier", {}).get(self.identifier_scheme_doctype)
		if not providers:
			frappe.throw(
				_(
					"No application issues identifiers for {0}. Clear Identifier Scheme, or install the application that provides it."
				).format(self.identifier_scheme_doctype),
				title=_("No Identifier Provider"),
			)
		identifier = frappe.get_attr(providers[-1])(
			self.identifier_scheme_doctype, self.identifier_scheme, self
		)
		self.db_set("container_identifier", identifier)
		self.add_identifier_barcode(identifier)

	def add_identifier_barcode(self, identifier: str):
		barcode = frappe.new_doc("Item Barcode")
		barcode.parenttype = "Handling Unit"
		barcode.parent = self.handling_unit
		# the symbology belongs to the identifier scheme and its label, not to BEAM
		barcode.barcode = identifier
		barcode.flags.ignore_permissions = True
		barcode.save()

	def get_projected_members(self) -> list[str]:
		"""The bundle's direct contents as they will be once this entry is applied"""
		members: dict[str, None] = dict.fromkeys(self.get_current_members())
		for row in self.items:
			hu = row.container_handling_unit or self.get_row_handling_unit(row)
			if not hu:
				continue
			if self.purpose == "Pack":
				members[hu] = None
			else:
				members.pop(hu, None)
		return list(members)

	def get_current_members(self) -> list[str]:
		if not self.handling_unit:
			return []
		exclude = self.name if self.docstatus == 0 else None
		return get_bundle_contents(self.handling_unit, exclude)

	def get_projected_leaf_balances(self) -> list:
		return get_held_balances(self.get_projected_members())

	def get_displayed_members(self) -> list[str]:
		"""A draft shows what it will hold once submitted; anything else shows what it holds now"""
		return self.get_projected_members() if self.docstatus == 0 else self.get_current_members()


def get_held_balances(members: list[str]) -> list:
	"""Balances of the Handling Units holding stock anywhere beneath these members"""
	if not members:
		return []
	leaves = [node.handling_unit for node in walk(members) if node.is_leaf]
	balances = get_leaf_balances(leaves)
	return [balances[leaf] for leaf in leaves if leaf in balances and not balances[leaf].depleted]


@frappe.whitelist()
def get_entry_preview(doc: str | dict) -> dict:
	"""
	The Bundle Contents display: level, totals and the whole arrangement as a tree. A draft is
	shown as it will be once submitted, so the form keeps up while rows are still being scanned.
	"""
	frappe.has_permission("Handling Unit Bundle Entry", "read", throw=True)
	entry = frappe.get_doc(frappe.parse_json(doc))
	for row in entry.items:
		row.container_handling_unit = entry.get_row_handling_unit(row)
	members = entry.get_displayed_members()
	balances = get_held_balances(members)
	if entry.docstatus == 0:
		entry.set_kit_complete()
	return {
		"level": entry.level,
		"kit_complete": entry.kit_complete,
		"warehouses": sorted({balance.warehouse for balance in balances}),
		"leaf_count": len(balances),
		"total_qty": sum(flt(balance.qty) for balance in balances),
		"tree": get_contents_tree(members),
	}
