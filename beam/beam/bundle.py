# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Containment for nested Handling Units. See docs/handling_unit_nesting.md.

This module is the only sanctioned read path for containment: no other code queries the
Handling Unit Bundle Entry tables directly. Contents are never stored. A bundle's contents are
the fold of its submitted entries in posting order, where Pack adds and Unpack removes, and
every walk resolves a whole level of the arrangement per query rather than one row at a time.
"""

from collections import Counter

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import comma_and, comma_or, flt, now_datetime

# A ceiling on every walk, independent of Nest Cap, so that cyclic data or an arrangement
# grandfathered under a higher cap can never hang a traversal
WALK_LIMIT = 100


def get_bundle_contents(bundle: str, exclude_entry: str | None = None) -> list[str]:
	"""Direct contents of a bundle, as Handling Unit names"""
	return get_contents_map([bundle], exclude_entry).get(bundle, [])


def get_contents_map(bundles: list[str], exclude_entry: str | None = None) -> dict[str, list[str]]:
	"""Direct contents of several bundles in one query, folded per bundle in posting order"""
	bundles = [bundle for bundle in bundles if bundle]
	if not bundles:
		return {}
	entry = frappe.qb.DocType("Handling Unit Bundle Entry")
	row = frappe.qb.DocType("Packed Handling Unit")
	query = (
		frappe.qb.from_(entry)
		.join(row)
		.on(row.parent == entry.name)
		.select(entry.handling_unit, entry.purpose, row.container_handling_unit)
		.where(entry.handling_unit.isin(bundles))
		.where(entry.docstatus == 1)
		.where(row.parenttype == "Handling Unit Bundle Entry")
		.orderby(entry.posting_datetime)
		.orderby(entry.creation)
		.orderby(row.idx)
	)
	if exclude_entry:
		query = query.where(entry.name != exclude_entry)
	return fold_events(query.run(as_dict=True))


def fold_events(events: list[frappe._dict]) -> dict[str, list[str]]:
	contents: dict[str, dict] = {}
	for event in events:
		members = contents.setdefault(event.handling_unit, {})
		if event.purpose == "Pack":
			members[event.container_handling_unit] = None
		else:
			members.pop(event.container_handling_unit, None)
	return {bundle: list(members) for bundle, members in contents.items()}


def get_bundle_leaves(bundle: str) -> list[str]:
	"""Contents recursively, down to the Handling Units holding stock"""
	return [node.handling_unit for node in walk([bundle]) if node.is_leaf]


def walk(roots: list[str], overrides: dict | None = None) -> list[frappe._dict]:
	"""
	Breadth-first descent from each root, one contents query per level and one container test
	for the whole arrangement. `overrides` substitutes a projected member list for a bundle
	whose draft entry has not been submitted yet, so validation can judge the arrangement as it
	would be rather than as it is.
	"""
	overrides = overrides or {}
	nodes = [frappe._dict(handling_unit=root, parent=None, root=root, depth=1) for root in roots]
	# visited per root, so roots that overlap still each get their whole arrangement
	level, seen = list(nodes), {root: {root} for root in roots}
	for depth in range(2, WALK_LIMIT + 2):
		level = descend(level, depth, overrides, seen)
		if not level:
			break
		nodes.extend(level)
	mark_leaves(nodes, overrides)
	return nodes


def descend(level: list[frappe._dict], depth: int, overrides: dict, seen: dict) -> list:
	queried = get_contents_map(
		[node.handling_unit for node in level if node.handling_unit not in overrides]
	)
	children = []
	for node in level:
		members = overrides.get(node.handling_unit, queried.get(node.handling_unit, []))
		for member in members:
			if member in seen[node.root]:
				continue
			seen[node.root].add(member)
			children.append(
				frappe._dict(handling_unit=member, parent=node.handling_unit, root=node.root, depth=depth)
			)
	return children


def mark_leaves(nodes: list[frappe._dict], overrides: dict) -> None:
	containers = is_container([node.handling_unit for node in nodes]) | set(overrides)
	for node in nodes:
		node.is_leaf = node.handling_unit not in containers


def depth_below(handling_unit: str, overrides: dict | None = None) -> int:
	"""Levels from this unit down to its deepest leaf. A Handling Unit holding stock is 1."""
	return max(node.depth for node in walk([handling_unit], overrides))


def root_of(handling_unit: str) -> str:
	"""The outermost bundle above this unit, or the unit itself where it is loose"""
	ancestors = get_ancestors(handling_unit)
	return ancestors[-1] if ancestors else handling_unit


def get_ancestors(handling_unit: str, exclude_entry: str | None = None) -> list[str]:
	"""Every bundle above this unit, innermost first"""
	return get_ancestry([handling_unit], exclude_entry).get(handling_unit, [])


def get_ancestry(
	handling_units: list[str], exclude_entry: str | None = None
) -> dict[str, list[str]]:
	"""
	Every bundle above each unit, innermost first, climbing one level for all of them per query.
	A unit's climb stops on a repeat so cyclic data ends.
	"""
	ancestry: dict[str, list[str]] = {hu: [] for hu in handling_units if hu}
	# each unit mapped to the highest bundle its climb has reached
	climbing = {hu: hu for hu in ancestry}
	for _step in range(WALK_LIMIT):
		containers = get_containers_of(list(set(climbing.values())), exclude_entry)
		climbing = {
			hu: containers[top]
			for hu, top in climbing.items()
			if is_new_ancestor(hu, containers.get(top), ancestry[hu])
		}
		if not climbing:
			break
		for hu, container in climbing.items():
			ancestry[hu].append(container)
	return ancestry


def is_new_ancestor(handling_unit: str, container: str | None, ancestors: list[str]) -> bool:
	return bool(container) and container != handling_unit and container not in ancestors


def get_container_of(handling_unit: str) -> str | None:
	"""The bundle a unit is currently inside, or None where it is loose"""
	return get_containers_of([handling_unit]).get(handling_unit)


def get_containers_of(
	handling_units: list[str], exclude_entry: str | None = None
) -> dict[str, str]:
	"""Reverse lookup in one query. The newest submitted entry mentioning each unit wins."""
	handling_units = [hu for hu in handling_units if hu]
	if not handling_units:
		return {}
	entry = frappe.qb.DocType("Handling Unit Bundle Entry")
	row = frappe.qb.DocType("Packed Handling Unit")
	query = (
		frappe.qb.from_(entry)
		.join(row)
		.on(row.parent == entry.name)
		.select(entry.handling_unit, entry.purpose, row.container_handling_unit)
		.where(row.container_handling_unit.isin(handling_units))
		.where(row.parenttype == "Handling Unit Bundle Entry")
		.where(entry.docstatus == 1)
		.orderby(entry.posting_datetime)
		.orderby(entry.creation)
		.orderby(row.idx)
	)
	if exclude_entry:
		query = query.where(entry.name != exclude_entry)
	latest = {event.container_handling_unit: event for event in query.run(as_dict=True)}
	return {hu: event.handling_unit for hu, event in latest.items() if event.purpose == "Pack"}


def get_bundle_entry_of(handling_unit: str) -> str | None:
	"""The latest submitted entry for which the unit is the bundle, or None where it holds stock"""
	entries = frappe.get_all(
		"Handling Unit Bundle Entry",
		filters={"handling_unit": handling_unit, "docstatus": 1},
		order_by="posting_datetime desc, creation desc",
		pluck="name",
		limit=1,
	)
	return entries[0] if entries else None


def is_container(handling_units: list[str]) -> set[str]:
	"""
	The subset of these units that are bundles, in one query. A draft counts: its Handling Unit
	exists to hold other units from the moment it is saved, so it must never take on stock.
	"""
	handling_units = list({hu for hu in handling_units if hu})
	if not handling_units:
		return set()
	return set(
		frappe.get_all(
			"Handling Unit Bundle Entry",
			filters={"handling_unit": ["in", handling_units], "docstatus": ["<", 2]},
			pluck="handling_unit",
			distinct=True,
		)
	)


def get_bundle_types(bundles: list[str]) -> dict[str, str]:
	"""Each bundle's type, read from its latest entry that has not been cancelled"""
	bundles = list({bundle for bundle in bundles if bundle})
	if not bundles:
		return {}
	entries = frappe.get_all(
		"Handling Unit Bundle Entry",
		filters={"handling_unit": ["in", bundles], "docstatus": ["<", 2]},
		fields=["handling_unit", "bundle_type"],
		order_by="posting_datetime asc, creation asc",
	)
	return {entry.handling_unit: entry.bundle_type for entry in entries}


def get_leaf_balances(handling_units: list[str]) -> dict[str, frappe._dict]:
	"""
	Item, UOM, quantity and warehouse of each Handling Unit holding stock, in one query.
	Balances are taken per warehouse, so a unit transferred in full reports where it is now
	rather than where it was received. A unit with no positive balance is reported as depleted.
	"""
	handling_units = list({hu for hu in handling_units if hu})
	if not handling_units:
		return {}
	sle = frappe.qb.DocType("Stock Ledger Entry")
	rows = (
		frappe.qb.from_(sle)
		.select(
			sle.handling_unit,
			sle.warehouse,
			sle.item_code,
			sle.stock_uom,
			Sum(sle.actual_qty).as_("qty"),
		)
		.where(sle.handling_unit.isin(handling_units))
		.where(sle.is_cancelled == 0)
		.groupby(sle.handling_unit, sle.warehouse, sle.item_code, sle.stock_uom)
	).run(as_dict=True)
	return summarise_balances(rows)


def summarise_balances(rows: list[frappe._dict]) -> dict[str, frappe._dict]:
	balances: dict[str, frappe._dict] = {}
	for row in rows:
		balance = balances.setdefault(
			row.handling_unit,
			frappe._dict(item_code=row.item_code, stock_uom=row.stock_uom, qty=0.0, warehouse=None),
		)
		if flt(row.qty) > flt(balance.qty):
			balance.update(qty=flt(row.qty), warehouse=row.warehouse)
	for balance in balances.values():
		balance.depleted = flt(balance.qty) <= 0
	return balances


def get_packed_row_details(handling_unit: str) -> frappe._dict:
	"""Display values for one Packed Handling Unit row: a leaf's own, or a bundle's totals"""
	return get_packed_rows_details([handling_unit]).get(handling_unit, frappe._dict())


def get_packed_rows_details(handling_units: list[str]) -> dict[str, frappe._dict]:
	containers = is_container(handling_units)
	types = get_bundle_types(list(containers))
	nodes = walk(list(containers)) if containers else []
	leaves_by_root: dict[str, list[str]] = {}
	for node in nodes:
		if node.is_leaf:
			leaves_by_root.setdefault(node.root, []).append(node.handling_unit)
	leaf_units = [hu for hu in handling_units if hu not in containers]
	balances = get_leaf_balances(leaf_units + [node.handling_unit for node in nodes if node.is_leaf])
	details = {}
	for hu in handling_units:
		if hu in containers:
			details[hu] = summarise_bundle(hu, types.get(hu), leaves_by_root.get(hu, []), balances)
		else:
			details[hu] = summarise_leaf(hu, balances.get(hu))
	return details


def summarise_leaf(handling_unit: str, balance: frappe._dict | None) -> frappe._dict:
	balance = balance or frappe._dict()
	return frappe._dict(
		container_handling_unit=handling_unit,
		packed_bundle_type=None,
		item_code=balance.item_code,
		stock_qty=flt(balance.qty),
		stock_uom=balance.stock_uom,
		warehouse=balance.warehouse,
	)


def summarise_bundle(
	handling_unit: str, bundle_type: str | None, leaves: list[str], balances: dict
) -> frappe._dict:
	held = [balances[leaf] for leaf in leaves if leaf in balances and not balances[leaf].depleted]
	uoms = {balance.stock_uom for balance in held}
	warehouses = {balance.warehouse for balance in held}
	return frappe._dict(
		container_handling_unit=handling_unit,
		packed_bundle_type=bundle_type,
		item_code=None,
		stock_qty=sum(flt(balance.qty) for balance in held),
		stock_uom=uoms.pop() if len(uoms) == 1 else None,
		warehouse=warehouses.pop() if len(warehouses) == 1 else None,
	)


def get_nest_cap(company: str | None) -> int:
	if not company:
		return 1
	return frappe.db.get_value("BEAM Settings", {"company": company}, "nest_cap") or 1


def evaluate_nesting_policy(entry, rows: list) -> list[str]:
	"""
	Every nesting rule the entry breaks, as messages rather than an exception so the caller can
	report them together. Every built-in check is inert at its default. Apps registered on the
	`beam_nesting_policy` hook then receive the accumulated violations and may add to them,
	clear them, or replace them outright.
	"""
	policy = frappe.get_cached_doc("Handling Unit Bundle Type", entry.bundle_type)
	violations = []
	if entry.purpose == "Pack":
		violations += check_max_members(entry, policy)
		violations += check_loose_handling_units(rows, policy)
		violations += check_allowed_child_types(rows, policy)
		violations += check_uniform_members(entry, rows, policy)
	violations += check_seal_when_contained(entry, policy)
	for method in frappe.get_hooks("beam_nesting_policy"):
		violations = frappe.get_attr(method)(entry, rows, violations)
	return violations


def check_max_members(entry, policy) -> list[str]:
	members = entry.get_projected_members()
	if not policy.max_members or len(members) <= policy.max_members:
		return []
	return [
		_("A {0} holds at most {1}, and this entry would leave {2} inside.").format(
			policy.name, policy.max_members, len(members)
		)
	]


def check_loose_handling_units(rows: list, policy) -> list[str]:
	if policy.allow_loose_handling_units:
		return []
	return [
		_("{0} does not allow loose Handling Units. Pack {1} into a container first.").format(
			policy.name, row.container_handling_unit
		)
		for row in rows
		if not row.packed_bundle_type
	]


def check_allowed_child_types(rows: list, policy) -> list[str]:
	allowed = {link.bundle_type for link in policy.allowed_child_types}
	if not allowed:
		return []
	return [
		_("{0} accepts only {1}. Row {2} is a {3}.").format(
			policy.name, comma_or(sorted(allowed), add_quotes=False), row.idx, row.packed_bundle_type
		)
		for row in rows
		if row.packed_bundle_type and row.packed_bundle_type not in allowed
	]


def check_uniform_members(entry, rows: list, policy) -> list[str]:
	if not policy.require_uniform_members:
		return []
	members = entry.get_projected_members()
	types = get_bundle_types(members)
	kinds = {types.get(member) or _("Handling Unit holding stock") for member in members}
	if len(kinds) <= 1:
		return []
	return [
		_("{0} requires uniform members, but this entry mixes {1}.").format(
			policy.name, comma_and(sorted(kinds), add_quotes=False)
		)
	]


def check_seal_when_contained(entry, policy) -> list[str]:
	if not policy.seal_when_contained or not entry.handling_unit:
		return []
	container = get_container_of(entry.handling_unit)
	if not container:
		return []
	return [
		_(
			"{0} is inside {1}, and a {2} cannot be packed or unpacked while it is contained. Unpack it from {1} first."
		).format(entry.handling_unit, container, policy.name)
	]


@frappe.whitelist()
def get_bundle_tree(bundle: str) -> list[frappe._dict]:
	"""
	One level of the arrangement under a bundle. A display expands a branch by calling this again
	for that member, so inspecting a deep bundle costs one call per branch opened rather than a
	walk of the whole arrangement. Nodes carry `value` and `expandable` as `frappe.ui.Tree` reads them.
	"""
	frappe.has_permission("Handling Unit Bundle Entry", "read", throw=True)
	members = get_bundle_contents(bundle)
	details = get_packed_rows_details(members)
	identifiers = get_container_identifiers([hu for hu in members if details[hu].packed_bundle_type])
	return [
		frappe._dict(
			value=hu,
			handling_unit=hu,
			expandable=bool(details[hu].packed_bundle_type),
			container_identifier=identifiers.get(hu),
			**details[hu],
		)
		for hu in members
	]


def get_container_identifiers(bundles: list[str]) -> dict[str, str]:
	if not bundles:
		return {}
	entries = frappe.get_all(
		"Handling Unit Bundle Entry",
		filters={
			"handling_unit": ["in", bundles],
			"docstatus": 1,
			"container_identifier": ["is", "set"],
		},
		fields=["handling_unit", "container_identifier"],
		order_by="posting_datetime asc, creation asc",
	)
	return {entry.handling_unit: entry.container_identifier for entry in entries}


def get_contents_tree(members: list[str]) -> list[frappe._dict]:
	"""
	The whole arrangement beneath these members, nested, for a form that shows a bundle's
	contents at once. Built from one walk, so it costs one query per level, not per node.
	"""
	if not members:
		return []
	nodes = walk(members)
	leaves = [node.handling_unit for node in nodes if node.is_leaf]
	bundles = [node.handling_unit for node in nodes if not node.is_leaf]
	details = TreeDetails(
		get_leaf_balances(leaves), get_bundle_types(bundles), get_container_identifiers(bundles)
	)
	built: dict[tuple, frappe._dict] = {}
	roots = []
	# a walk lists every parent before its children
	for node in nodes:
		branch = details.describe(node)
		built[(node.root, node.handling_unit)] = branch
		if node.parent:
			built[(node.root, node.parent)].children.append(branch)
		else:
			roots.append(branch)
	return roots


class TreeDetails:
	def __init__(self, balances: dict, types: dict, identifiers: dict):
		self.balances = balances
		self.types = types
		self.identifiers = identifiers

	def describe(self, node: frappe._dict) -> frappe._dict:
		branch = frappe._dict(handling_unit=node.handling_unit, is_leaf=node.is_leaf, children=[])
		if node.is_leaf:
			balance = self.balances.get(node.handling_unit) or frappe._dict()
			branch.update(
				item_code=balance.item_code, stock_qty=flt(balance.qty), stock_uom=balance.stock_uom
			)
		else:
			branch.update(
				bundle_type=self.types.get(node.handling_unit),
				container_identifier=self.identifiers.get(node.handling_unit),
			)
		return branch


@frappe.whitelist()
def get_bundle_preview(barcode: str) -> dict | None:
	"""
	What scanning a bundle onto a document would add, shown before it is added. Accepts the
	bundle's Handling Unit or its container identifier, as a scan would.
	"""
	frappe.has_permission("Handling Unit Bundle Entry", "read", throw=True)
	from beam.beam.scan import get_barcode_context

	context = get_barcode_context(barcode)
	if not context or context.doc.doctype != "Handling Unit" or not is_container([context.doc.name]):
		return None
	bundle = context.doc.name
	balances = get_leaf_balances(get_bundle_leaves(bundle))
	held = {leaf: balance for leaf, balance in balances.items() if not balance.depleted}
	return {
		"bundle": bundle,
		"bundle_type": get_bundle_types([bundle]).get(bundle),
		"level": depth_below(bundle),
		"leaves": [
			{
				"handling_unit": leaf,
				"item_code": balance.item_code,
				"stock_qty": balance.qty,
				"stock_uom": balance.stock_uom,
				"warehouse": balance.warehouse,
			}
			for leaf, balance in held.items()
		],
	}


def get_row_handling_units(doc, fieldnames=("handling_unit", "to_handling_unit")) -> list[str]:
	return [
		row.get(field) for row in doc.get("items") or [] for field in fieldnames if row.get(field)
	]


def validate_no_bundle_on_rows(doc, method: str | None = None) -> None:
	"""
	A bundle never appears on a transaction row. One batched test per document makes "only
	Handling Units holding stock reach the Stock Ledger" structural rather than conventional.
	"""
	bundles = is_container(get_row_handling_units(doc))
	if not bundles:
		return
	messages = [
		_(
			"Row {0}: Handling Unit {1} is a bundle, not a Handling Unit holding stock. Scan the bundle instead and it will expand to one row per item."
		).format(row.idx, row.get(field))
		for row in doc.get("items")
		for field in ("handling_unit", "to_handling_unit")
		if row.get(field) in bundles
	]
	frappe.throw("<br>".join(messages), title=_("Bundle Used on a Row"))


def validate_member_transactions(doc, method: str | None = None) -> None:
	"""
	Reject rows whose Handling Unit is inside a bundle, at any level, whose type restricts its
	contents: a restricting Pallet covers the units in its Boxes as well as any loose on it.
	Moving a bundle whole is not reaching into it, so a bundle this document takes entirely
	releases its own restriction, though not one held by a bundle further up.
	"""
	rows = [row for row in doc.get("items") or [] if row.get("handling_unit")]
	ancestry = get_ancestry([row.handling_unit for row in rows])
	restricting = get_restricting_types(ancestry)
	if not restricting:
		return
	enforced = set(restricting) - get_bundles_taken_whole(doc, list(restricting))
	messages = [
		_(
			"Row {0}: {1} is inside {2}, a {3}, which has Restrict Member Transactions enabled. Unpack it first, or include everything in {2} at its full quantity."
		).format(row.idx, row.handling_unit, bundle, restricting[bundle])
		for row, bundle in get_restricted_rows(rows, ancestry, enforced)
	]
	if messages:
		frappe.throw("<br>".join(messages), title=_("Handling Unit Is Packed"))


def get_restricting_types(ancestry: dict[str, list[str]]) -> dict[str, str]:
	"""The bundles above these units whose type restricts their contents, with that type"""
	types = get_bundle_types([bundle for ancestors in ancestry.values() for bundle in ancestors])
	return {
		bundle: bundle_type
		for bundle, bundle_type in types.items()
		if frappe.get_cached_value(
			"Handling Unit Bundle Type", bundle_type, "restrict_member_transactions"
		)
	}


def get_bundles_taken_whole(doc, bundles: list[str]) -> set[str]:
	"""The bundles this document takes entirely: every unit beneath them holding stock, in full"""
	leaves: dict[str, list[str]] = {bundle: [] for bundle in bundles}
	for node in walk(bundles):
		if node.is_leaf:
			leaves[node.root].append(node.handling_unit)
	balances = get_leaf_balances([leaf for group in leaves.values() for leaf in group])
	taken = get_quantities_taken(doc)
	return {
		bundle
		for bundle, group in leaves.items()
		if all(is_taken_in_full(balances.get(leaf), taken.get(leaf)) for leaf in group)
	}


def get_quantities_taken(doc) -> dict[str, float]:
	"""Stock each Handling Unit gives up on this document, in its stock UOM"""
	qty_field = "transfer_qty" if doc.doctype == "Stock Entry" else "stock_qty"
	taken: dict[str, float] = {}
	for row in doc.get("items") or []:
		if row.get("handling_unit") and takes_stock(doc, row):
			taken[row.handling_unit] = taken.get(row.handling_unit, 0.0) + abs(flt(row.get(qty_field)))
	return taken


def takes_stock(doc, row) -> bool:
	"""Whether this row takes stock out of its Handling Unit. A Stock Reconciliation counts it."""
	if doc.doctype == "Stock Entry":
		return bool(row.get("s_warehouse"))
	if doc.doctype in ("Sales Invoice", "Purchase Invoice") and not doc.get("update_stock"):
		return False
	if doc.doctype in ("Delivery Note", "Sales Invoice"):
		return not doc.get("is_return")
	return doc.doctype in ("Purchase Receipt", "Purchase Invoice") and bool(doc.get("is_return"))


def is_taken_in_full(balance: frappe._dict | None, taken: float | None) -> bool:
	if not balance or balance.depleted:
		return True
	precision = frappe.get_precision("Stock Ledger Entry", "actual_qty")
	return flt(taken, precision) >= flt(balance.qty, precision)


def get_restricted_rows(rows: list, ancestry: dict, enforced: set[str]) -> list[tuple]:
	"""Each row a restriction holds, with the nearest bundle above it enforcing one"""
	restricted = []
	for row in rows:
		bundle = next((bundle for bundle in ancestry[row.handling_unit] if bundle in enforced), None)
		if bundle:
			restricted.append((row, bundle))
	return restricted


def reconcile_contained_members(doc, method: str | None = None) -> None:
	"""
	Keep containment truthful after a stock transaction. Any packed Handling Unit the voucher
	used up, or moved away from the rest of its bundle, is removed from that bundle by a
	system-generated Unpack naming the voucher. Runs after the ledger is written, so depletion is
	judged on posted quantities. On cancel the removals are re-evaluated rather than reversed:
	a cancellation can return a unit to its bundle, but only where it is genuinely back.
	"""
	touched = set(get_row_handling_units(doc))
	if doc.docstatus == 2:
		# a system-generated Unpack names this voucher, and must not block cancelling it
		doc.ignore_linked_doctypes = (
			*(doc.get("ignore_linked_doctypes") or ()),
			"Handling Unit Bundle Entry",
		)
		touched |= restore_system_unpacks(doc)
	containers = get_containers_of(list(touched))
	if not containers:
		return
	MemberReconciliation(doc, touched, containers).run()


def restore_system_unpacks(doc) -> set[str]:
	"""Cancel the system-generated Unpacks this voucher caused, where every unit in them is back"""
	restored = set()
	for name in frappe.get_all(
		"Handling Unit Bundle Entry",
		filters={
			"system_generated": 1,
			"docstatus": 1,
			"voucher_type": doc.doctype,
			"voucher_no": doc.name,
		},
		pluck="name",
	):
		entry = frappe.get_doc("Handling Unit Bundle Entry", name)
		members = [row.container_handling_unit for row in entry.items]
		if all(is_back_in_bundle(member, entry) for member in members):
			entry.flags.ignore_permissions = True
			entry.cancel()
			restored.update(members)
	return restored


def is_back_in_bundle(member: str, entry) -> bool:
	if get_container_of(member):
		return False
	member_nodes = walk([member])
	member_leaves = [node.handling_unit for node in member_nodes if node.is_leaf]
	member_units = {node.handling_unit for node in member_nodes}
	root = root_of(entry.handling_unit)
	home_leaves = [
		node.handling_unit
		for node in walk([root])
		if node.is_leaf and node.handling_unit not in member_units
	]
	balances = get_leaf_balances(member_leaves + home_leaves)
	held = [
		balances[leaf] for leaf in member_leaves if leaf in balances and not balances[leaf].depleted
	]
	if not held:
		return False
	home = {
		balances[leaf].warehouse
		for leaf in home_leaves
		if leaf in balances and not balances[leaf].depleted
	}
	return not home or {balance.warehouse for balance in held} <= home


class MemberReconciliation:
	"""
	Works out which packed units a voucher separated from their bundles. Each affected
	arrangement is judged from its root: its home warehouse is where the units the voucher did
	not touch still are, and anything the voucher touched that is now elsewhere comes out of the
	innermost bundle that still holds it together.
	"""

	def __init__(self, doc, touched: set[str], containers: dict[str, str]):
		self.doc = doc
		self.touched = touched
		self.containers = containers
		self.removals: dict[str, list[str]] = {}

	def run(self) -> None:
		for root in {root_of(container) for container in set(self.containers.values())}:
			self.reconcile_arrangement(root)
		for bundle, members in self.removals.items():
			create_system_unpack(bundle, members, self.doc)

	def reconcile_arrangement(self, root: str) -> None:
		self.nodes = walk([root])
		self.children: dict[str, list[frappe._dict]] = {}
		for node in self.nodes[1:]:
			self.children.setdefault(node.parent, []).append(node)
		leaves = [node.handling_unit for node in self.nodes if node.is_leaf]
		self.balances = get_leaf_balances(leaves)
		self.remove_depleted(leaves)
		home = self.get_home_warehouse(leaves)
		if home:
			self.separate(root, home)

	def remove_depleted(self, leaves: list[str]) -> None:
		for node in self.nodes:
			if node.handling_unit in self.touched and node.is_leaf and self.is_depleted(node.handling_unit):
				self.remove(node.parent, node.handling_unit)

	def get_home_warehouse(self, leaves: list[str]) -> str | None:
		held = [leaf for leaf in leaves if not self.is_depleted(leaf)]
		untouched = [leaf for leaf in held if leaf not in self.touched]
		counts = Counter(self.balances[leaf].warehouse for leaf in untouched or held)
		return counts.most_common(1)[0][0] if counts else None

	def separate(self, bundle: str, home: str) -> None:
		for node in self.children.get(bundle, []):
			if not self.touches(node):
				continue
			if node.is_leaf:
				self.separate_leaf(node, home)
				continue
			warehouses = self.held_warehouses(node)
			if warehouses and home not in warehouses and len(warehouses) == 1:
				self.remove(bundle, node.handling_unit)
			else:
				self.separate(node.handling_unit, home)

	def separate_leaf(self, node: frappe._dict, home: str) -> None:
		if self.is_depleted(node.handling_unit):
			return
		if self.balances[node.handling_unit].warehouse != home:
			self.remove(node.parent, node.handling_unit)

	def touches(self, node: frappe._dict) -> bool:
		return any(leaf in self.touched for leaf in self.leaves_under(node))

	def held_warehouses(self, node: frappe._dict) -> set[str]:
		return {
			self.balances[leaf].warehouse for leaf in self.leaves_under(node) if not self.is_depleted(leaf)
		}

	def leaves_under(self, node: frappe._dict) -> list[str]:
		if node.is_leaf:
			return [node.handling_unit]
		return [
			leaf for child in self.children.get(node.handling_unit, []) for leaf in self.leaves_under(child)
		]

	def is_depleted(self, leaf: str) -> bool:
		return leaf not in self.balances or self.balances[leaf].depleted

	def remove(self, bundle: str, member: str) -> None:
		members = self.removals.setdefault(bundle, [])
		if member not in members:
			members.append(member)


def create_system_unpack(bundle: str, members: list[str], voucher) -> None:
	"""Record a removal the system made, naming the voucher that caused it"""
	entry = frappe.new_doc("Handling Unit Bundle Entry")
	entry.update(
		{
			"company": voucher.get("company"),
			"purpose": "Unpack",
			"posting_datetime": now_datetime(),
			"handling_unit": bundle,
			"bundle_type": get_bundle_types([bundle]).get(bundle),
			"system_generated": 1,
			"voucher_type": voucher.doctype,
			"voucher_no": voucher.name,
		}
	)
	for member in members:
		entry.append("items", {"packed_doctype": "Handling Unit", "packed_handling_unit": member})
	entry.flags.ignore_permissions = True
	entry.insert()
	entry.submit()


@frappe.whitelist()
def count_bundles_exceeding(nest_cap: int, company: str) -> dict:
	"""How many existing arrangements are deeper than a proposed Nest Cap, and the deepest"""
	frappe.has_permission("BEAM Settings", "read", throw=True)
	bundles = frappe.get_all(
		"Handling Unit Bundle Entry",
		filters={"company": company, "docstatus": 1},
		pluck="handling_unit",
		distinct=True,
	)
	contained = get_containers_of(bundles)
	roots = [bundle for bundle in bundles if bundle not in contained]
	depths: Counter[str] = Counter()
	for node in walk(roots) if roots else []:
		depths[node.root] = max(depths[node.root], node.depth)
	exceeding = [depth for depth in depths.values() if depth > int(nest_cap)]
	return {"count": len(exceeding), "deepest": max(depths.values(), default=0)}
