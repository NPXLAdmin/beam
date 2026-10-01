# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.model.document import Document

from beam.beam.bundle import count_bundles_exceeding


class BEAMSettings(Document):
	if TYPE_CHECKING:
		from frappe.types import DF

		acknowledge_deeper_bundles: DF.Check
		enable_scan_to_login: DF.Literal["Not Allowed", "Mobile Users Only", "All Users"]
		nest_cap: DF.Int
		restrict_ip: DF.SmallText | None

	def validate(self):
		self.validate_nest_cap_reduction()

	def on_update(self):
		self.clear_deeper_bundle_acknowledgement()

	def validate_nest_cap_reduction(self):
		"""
		Lowering Nest Cap below bundles that already exist needs acknowledging. Those bundles are
		never re-validated, so they stay usable; only new entries are held to the lower cap.
		"""
		previous = self.get_doc_before_save()
		if not previous or self.nest_cap >= (previous.nest_cap or 1) or self.acknowledge_deeper_bundles:
			return
		exceeding = count_bundles_exceeding(self.nest_cap, self.company)
		if exceeding["count"]:
			frappe.throw(
				_(
					"{0} existing bundles are deeper than a Nest Cap of {1}; the deepest is at level {2}. Confirm the change to keep them as they are and hold only new bundles to the lower cap."
				).format(exceeding["count"], self.nest_cap, exceeding["deepest"]),
				title=_("Bundles Deeper Than Nest Cap"),
			)

	def clear_deeper_bundle_acknowledgement(self):
		if self.acknowledge_deeper_bundles:
			self.db_set("acknowledge_deeper_bundles", 0)


@frappe.whitelist()
def create_beam_settings(company: str) -> str:
	beams = frappe.new_doc("BEAM Settings")
	beams.company = company
	beams.auto_barcode_doctypes = '["Item", "Warehouse", "User"]'
	beams.save()
	return beams


@frappe.whitelist()
def get_doctypes_with_item_barcodes() -> list[str]:
	"""Return all doctypes that have a Table field with options 'Item Barcode'."""
	existing_doctypes = set(frappe.get_all("DocType", pluck="name"))
	standard = frappe.get_all(
		"DocField",
		filters={"fieldtype": "Table", "options": "Item Barcode"},
		pluck="parent",
	)
	custom = frappe.get_all(
		"Custom Field",
		filters={"fieldtype": "Table", "options": "Item Barcode"},
		pluck="dt",
	)
	return sorted(existing_doctypes.intersection(standard + custom))
