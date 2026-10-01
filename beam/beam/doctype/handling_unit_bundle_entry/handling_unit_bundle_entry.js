// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Handling Unit Bundle Entry', {
	setup(frm) {
		frm.set_query('bundle_type', () => ({ filters: { disabled: 0 } }))
	},
	onload(frm) {
		if (frm.is_new() && !frm.doc.company) {
			frm.set_value('company', frappe.defaults.get_user_default('Company'))
		}
	},
	refresh(frm) {
		frm.trigger('items_changed')
	},
	product_bundle(frm) {
		frm.trigger('items_changed')
	},
	purpose(frm) {
		frm.trigger('items_changed')
	},
	// rows are added by scanning as well as by hand, and both end here
	items_changed(frm) {
		if (frm.doc.docstatus != 0) {
			return
		}
		frappe
			.xcall('beam.beam.doctype.handling_unit_bundle_entry.handling_unit_bundle_entry.get_entry_preview', {
				doc: frm.doc,
			})
			.then(preview => render_preview(frm, preview))
	},
})

frappe.ui.form.on('Packed Handling Unit', {
	packed_handling_unit(frm) {
		frm.trigger('items_changed')
	},
	items_remove(frm) {
		frm.trigger('items_changed')
	},
})

// The level is calculated rather than stored, and a draft's own rows count towards it, so show
// what the bundle will be once submitted
function render_preview(frm, preview) {
	frm.doc.level = preview.level
	frm.refresh_field('level')
	frm.dashboard.clear_headline()
	frm.dashboard.reset()
	if (frm.doc.product_bundle) {
		frm.dashboard.add_indicator(
			preview.kit_complete ? __('Kit Complete') : __('Kit Incomplete'),
			preview.kit_complete ? 'green' : 'orange'
		)
	}
	if (preview.warehouses.length > 1) {
		frm.dashboard.set_headline(__('Contents are in more than one warehouse: {0}', [preview.warehouses.join(', ')]))
	}
	if (preview.contents.length) {
		frm.dashboard.add_section(contents_table(preview.contents), __('Contents'))
	}
}

function contents_table(contents) {
	const rows = contents
		.map(
			row => `<tr>
				<td>${frappe.utils.escape_html(row.item_code)}</td>
				<td class="text-right">${format_number(row.qty)} ${frappe.utils.escape_html(row.stock_uom || '')}</td>
			</tr>`
		)
		.join('')
	return `<table class="table table-sm">
		<thead><tr><th>${__('Item')}</th><th class="text-right">${__('Quantity')}</th></tr></thead>
		<tbody>${rows}</tbody>
	</table>`
}
