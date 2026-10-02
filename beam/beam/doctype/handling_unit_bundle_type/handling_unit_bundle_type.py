# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class HandlingUnitBundleType(Document):
	def validate(self):
		self.validate_not_own_child()

	def validate_not_own_child(self):
		if any(link.bundle_type == self.name for link in self.allowed_child_types):
			frappe.throw(
				_("A {0} cannot be listed among its own Allowed Child Types.").format(self.name),
				title=_("Bundle Type Inside Itself"),
			)
