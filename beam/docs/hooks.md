<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Extending BEAM With Custom Hooks

<div class="byline">
  Claude Opus 5.5, Rohan Bansal, Robert Duncan, Heather Kusmierz, and Tyler Matteson 2026-10-01
</div>


BEAM can be extended by adding configurations to your application's `hooks.py`.

To make scanning available on a custom doctype, add a table field for "Item Barcode" directly in the doctype or via customize form. Then add a key that is a peer with "Item" in the example below.

To extend scanning functionality within a doctype, add a key that is a peer with "Delivery Note" in the example below.

```python
# hooks.py

beam_listview = {
	"Item": {
		"Delivery Note": [
			{"action": "filter", "doctype": "Delivery Note Item", "field": "item_code"},
			{"action": "filter", "doctype": "Packed Item", "field": "item_code"}
		],
	}
}

beam_frm = {
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
```
To add a custom JavaScript function, add the following hook to your application's `hooks.py`. An example implementation is available in the source code. 

```python
# hooks.py

beam_client = {
	"show_message": "custom_app.show_message"
}

```

A `beam_frm` action reads its value from the scan's target, as in `target.item_code` above. BEAM builds that target for Handling Units, Items and Serial Numbers; anything else scanned on a form you override is passed as its own `doctype` and `name`. To build a target for your application's doctypes, or to add values to the one BEAM built, register a builder in `beam_frm_target`. Builders are tried in order for every scan on a form with a `beam_frm` override. Each receives the barcode's context, the scan context, and BEAM's target (or `None` where BEAM builds none), and returns a target or `None` to pass to the next builder.

```python
# hooks.py

beam_frm_target = ["custom_app.scan.build_scan_target"]
```

```python
# custom_app/scan.py

def build_scan_target(barcode_doc, context, target=None):
	if barcode_doc.doc.doctype == "Shipment":
		# the form being scanned into, and the shipment that was scanned
		return frappe._dict(name=context.doc.get("name"), barcode_doc=barcode_doc.doc.name)
	if barcode_doc.doc.doctype == "Handling Unit" and target:
		target.batch_no = get_batch(target.handling_unit)
		return target
```

## Nesting hooks

[Nested Handling Units](./handling_unit_nesting.md) publish two hooks, so an application can apply its own nesting rules or issue container identifiers in its own format without changing BEAM.

`beam_nesting_policy` is a list of dotted paths. Each is called after BEAM has checked the bundle type's own rules, with the Handling Unit Bundle Entry, its rows, and the violations found so far, and returns the violation list. It may add to the list, clear it, or replace it.

```python
# hooks.py

beam_nesting_policy = ["custom_app.nesting.evaluate"]
```

```python
# custom_app/nesting.py

def evaluate(entry, rows, violations):
	if entry.bundle_type == "Pallet" and len(rows) > 40:
		violations.append("A pallet takes at most 40 cases per packing event")
	return violations
```

`beam_container_identifier` maps the doctype of an identifier scheme to the function that issues the next identifier for it. When a submitted Bundle Entry names a scheme of that doctype, BEAM calls the function with the scheme's doctype, its name, and the entry, stores the string it returns as the Container Identifier, and gives the container a barcode for it.

```python
# hooks.py

beam_container_identifier = {
	"SSCC Settings": "custom_app.sscc.next_identifier",
}
```

```python
# custom_app/sscc.py

def next_identifier(scheme_doctype, scheme_name, entry):
	settings = frappe.get_doc(scheme_doctype, scheme_name)
	return settings.next_serial_shipping_container_code()
```