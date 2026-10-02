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
		if (!frm.is_new() && frm.doc.docstatus === 0) {
			frm.add_custom_button(__('Scan Handling Unit'), () => scan_dialog(frm))
		}
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

// The level is calculated rather than stored, and a draft's own rows count towards it, so a
// draft shows what the bundle will be once submitted
function render_preview(frm, preview) {
	if (frm.doc.docstatus === 0) {
		frm.doc.level = preview.level
		frm.refresh_field('level')
	}
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
	render_contents(frm, preview)
}

function render_contents(frm, preview) {
	const field = frm.get_field('contents_preview')
	if (!field || !field.$wrapper) {
		return
	}
	const hint = frm.doc.handling_unit
		? ''
		: `<div class="text-muted" style="margin-bottom:.5rem">${__(
				'Save to create this bundle’s Handling Unit — it becomes scannable straight away, so a label can go on the container before it is filled.'
		  )}</div>`
	if (!preview.tree.length) {
		field.$wrapper.html(hint)
		return
	}
	field.$wrapper.html(`
		${hint}
		<div style="border:1px solid var(--border-color);border-radius:var(--border-radius-md);padding:.75rem 1rem">
			<div style="display:flex;gap:1.5rem;flex-wrap:wrap;margin-bottom:.5rem">
				${summary(__('Level'), preview.level ?? '')}
				${summary(__('Handling Units Holding Stock'), preview.leaf_count)}
				${summary(__('Total Quantity Held'), format_number(preview.total_qty))}
				${summary(__('Warehouse'), preview.warehouses.length === 1 ? preview.warehouses[0] : '—')}
			</div>
			${preview.tree.map(node => node_html(node, 0)).join('')}
		</div>`)
}

function summary(label, value) {
	return `<div><span class="text-muted">${label}</span> <b>${frappe.utils.escape_html(String(value))}</b></div>`
}

function node_html(node, depth) {
	const pad = `padding-left:${depth * 1.25 + 0.25}rem;padding-top:.2rem`
	const hu = frappe.utils.escape_html(node.handling_unit)
	if (node.is_leaf) {
		return `<div style="${pad}">
			<span class="indicator-pill green">${__('Stock')}</span>
			<b>${frappe.utils.escape_html(node.item_code || node.handling_unit)}</b>
			<span class="text-muted">${format_number(node.stock_qty || 0)} ${frappe.utils.escape_html(
				node.stock_uom || ''
			)} · ${hu}</span>
		</div>`
	}
	return `<div style="${pad}">
			<span class="indicator-pill blue">${frappe.utils.escape_html(node.bundle_type || __('Bundle'))}</span>
			<b>${hu}</b>
			<span class="text-muted">${frappe.utils.escape_html(node.container_identifier || '')}</span>
		</div>${node.children.map(child => node_html(child, depth + 1)).join('')}`
}

// Scan-driven row entry from the toolbar, for when no hardware scanner is attached. A scan goes
// through the same resolution as a scanner, so a bundle's label or container identifier works too.
function scan_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __('Scan Handling Unit'),
		fields: [
			{
				fieldname: 'code',
				fieldtype: 'Data',
				label: __('Handling Unit'),
				reqd: 1,
				description: __('Scan a Handling Unit, or the Handling Unit of another bundle.'),
			},
		],
		primary_action_label: __('Add'),
		primary_action(values) {
			const code = (values.code || '').trim()
			frappe
				.xcall('beam.beam.scan.scan', { barcode: code, context: { frm: frm.doctype, doc: frm.doc } })
				.then(actions => {
					if (!actions || !actions.length) {
						frappe.msgprint({
							title: __('Not Found'),
							message: __('{0} is not a known Handling Unit.', [code]),
							indicator: 'red',
						})
						return
					}
					window.scanHandler.dispatch(actions)
					dialog.hide()
					frappe.show_alert({ message: __('Added row {0}', [frm.doc.items.length]), indicator: 'green' })
				})
		},
	})
	dialog.show()
}
