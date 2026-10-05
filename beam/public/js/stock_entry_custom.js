// Copyright (c) 2025, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Stock Entry', {
	refresh(frm) {
		add_scan_bundle_button(frm)
	},
	async before_cancel(frm) {
		await set_recombine_handling_units(frm)
	},
	setup: function (frm) {
		frm.set_query('handling_unit', 'items', function (doc, cdt, cdn) {
			let row = locals[cdt][cdn]
			if (!row.item_code) {
				return
			}
			return {
				query: 'beam.beam.overrides.stock_entry.get_handling_units_for_item_code',
				filters: {
					item_code: row.item_code,
				},
			}
		})
	},
})

// Nesting is off until a company raises Nest Cap, so the button appears only where bundles can exist
function add_scan_bundle_button(frm) {
	const nest_cap = frappe.boot.beam?.settings?.[frm.doc.company]?.nest_cap || 1
	if (frm.doc.docstatus !== 0 || nest_cap <= 1) {
		return
	}
	frm.add_custom_button(__('Scan Bundle'), () => scan_bundle_dialog(frm), __('Handling Units'))
}

// Stands in for scanning a container's label: the scan goes through the same resolution as a
// hardware scanner, so every Handling Unit holding stock beneath the bundle becomes one row
function scan_bundle_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __('Scan Bundle'),
		fields: [
			{
				fieldname: 'bundle',
				fieldtype: 'Data',
				label: __('Handling Unit'),
				reqd: 1,
				description: __(
					'Scan the container’s label. Every Handling Unit holding stock beneath it becomes one row, at any depth.'
				),
				change: () => preview_bundle(dialog),
			},
			{ fieldname: 'preview', fieldtype: 'HTML' },
		],
		primary_action_label: __('Add Rows'),
		primary_action(values) {
			if (!dialog.bundle_preview) {
				frappe.msgprint(__('{0} is not a bundle.', [values.bundle]))
				return
			}
			add_bundle_rows(frm, dialog)
		},
	})
	dialog.show()
}

function preview_bundle(dialog) {
	const barcode = (dialog.get_value('bundle') || '').trim()
	dialog.bundle_preview = null
	if (!barcode) {
		return
	}
	frappe.xcall('beam.beam.bundle.get_bundle_preview', { barcode }).then(preview => {
		dialog.bundle_preview = preview
		dialog.get_field('preview').$wrapper.html(preview ? bundle_preview_html(preview) : '')
	})
}

function bundle_preview_html(preview) {
	const leaves = preview.leaves
		.map(
			leaf => `<div>${frappe.utils.escape_html(leaf.item_code || leaf.handling_unit)}
				<span class="text-muted">${format_number(leaf.stock_qty || 0)} ${frappe.utils.escape_html(
					leaf.stock_uom || ''
				)} · ${frappe.utils.escape_html(leaf.handling_unit)}</span></div>`
		)
		.join('')
	return `<div class="text-muted" style="margin-bottom:.35rem">
			${frappe.utils.escape_html(preview.bundle_type || '')} · ${__('Level')} ${preview.level} ·
			${__('{0} rows will be added', [preview.leaves.length])}
		</div>${leaves}`
}

function add_bundle_rows(frm, dialog) {
	const preview = dialog.bundle_preview
	if (!preview.leaves.length) {
		frappe.msgprint(__('That bundle holds nothing.'))
		return
	}
	frappe
		.xcall('beam.beam.scan.scan', { barcode: preview.bundle, context: { frm: frm.doctype, doc: frm.doc } })
		.then(actions => {
			if (actions && actions.length) {
				window.scanHandler.dispatch(actions)
			}
			dialog.hide()
			frappe.show_alert({
				message: __('Added {0} rows from {1}', [preview.leaves.length, preview.bundle]),
				indicator: 'green',
			})
		})
}

async function show_handling_unit_recombine_dialog(frm) {
	const data = await get_handling_units(frm)
	if (!data || !data.length) {
		return []
	}
	let fields = [
		{
			fieldtype: 'Data',
			fieldname: 'row_name',
			in_list_view: 0,
			read_only: 1,
			disabled: 0,
			hidden: 1,
		},
		{
			fieldtype: 'Data',
			fieldname: 'target_row_name',
			in_list_view: 0,
			read_only: 1,
			disabled: 0,
			hidden: 1,
		},
		{
			fieldtype: 'Link',
			fieldname: 'item_code',
			options: 'Item',
			in_list_view: 1,
			read_only: 1,
			disabled: 0,
			label: __('Item Code'),
			columns: 2,
		},
		{
			fieldtype: 'Data',
			fieldname: 'item_name',
			in_list_view: 0,
			disabled: 0,
			hidden: 1,
		},
		{
			fieldtype: 'Data',
			fieldname: 'handling_unit',
			label: __('Handling Unit'),
			in_list_view: 1,
			read_only: 1,
			columns: 2,
		},
		{
			fieldtype: 'Float',
			fieldname: 'remaining_qty',
			label: __('Remaining Qty'),
			in_list_view: 1,
			read_only: 1,
			columns: 1,
		},
		{
			fieldtype: 'Data',
			fieldname: 'to_handling_unit',
			label: __('Handling Unit to recombine'),
			in_list_view: 1,
			read_only: 1,
			columns: 2,
		},
		{
			fieldtype: 'Float',
			fieldname: 'transferred_qty',
			label: __('Transferred Qty'),
			in_list_view: 1,
			read_only: 1,
			columns: 1,
		},
	]

	return new Promise(resolve => {
		let dialog = new frappe.ui.Dialog({
			title: __('Please select Handling Units to re-combine'),
			fields: [
				{
					fieldname: 'handling_units',
					fieldtype: 'Table',
					cannot_add_rows: true,
					cannot_delete_rows: false,
					reqd: 1,
					data: data,
					get_data: () => {
						return data
					},
					fields: fields,
					description: __(
						'Please select Handling Units to re-combine. Unselected Handling Units will be returned to inventory with their new quantities and Handling Units'
					),
				},
			],
			primary_action: () => {
				let selected = dialog.fields_dict.handling_units.grid.get_selected_children()
				let to_recombine = []
				for (let row of selected) {
					to_recombine.push(row.row_name)
					if (row.target_row_name) {
						to_recombine.push(row.target_row_name)
					}
				}
				dialog.hide()
				return resolve(to_recombine)
			},
			primary_action_label: __('Cancel and Recombine'),
			size: 'extra-large',
		})
		dialog.show()
		// Pre-check all rows so recombine is the default behavior
		setTimeout(() => {
			const grid = dialog.fields_dict.handling_units.grid
			// Enable and check all rows
			if (grid.wrapper) {
				grid.wrapper.find('.grid-row-check').prop('disabled', false).prop('checked', true)
				// Hide the Delete button
				grid.wrapper.find('.grid-remove-rows').hide()
			}
			grid.grid_rows?.forEach(row => {
				if (row.doc) {
					row.doc.__checked = 1
					if (row.row) {
						row.row.find('.grid-row-check').prop('disabled', false).prop('checked', true)
					}
				}
			})
			grid.refresh()
		}, 200)
		dialog.get_close_btn()
	})
}

async function get_handling_units(frm) {
	let handling_units = []
	const transfer_types = ['Material Transfer', 'Send to Subcontractor', 'Material Transfer for Manufacture']

	for (const row of frm.doc.items) {
		if (!row.handling_unit) continue

		if (transfer_types.includes(frm.doc.purpose)) {
			// Material Transfer types: source and destination HU are on the same row
			if (!row.to_handling_unit) continue
			let remaining_qty = await get_handling_unit_stock_qty(frm.doc.name, row.handling_unit, row.s_warehouse)
			handling_units.push({
				row_name: row.name,
				item_code: row.item_code,
				item_name: row.item_name,
				handling_unit: row.handling_unit,
				to_handling_unit: row.to_handling_unit,
				remaining_qty: remaining_qty,
				transferred_qty: row.qty,
			})
		} else {
			// Repack/Manufacture/etc: source and target HUs are on separate rows
			// Only show source rows (those with s_warehouse); pair with matching target row
			if (!row.s_warehouse) continue
			let target_row = frm.doc.items.find(r => r.t_warehouse && r.handling_unit && r.item_code === row.item_code)
			let remaining_qty = await get_handling_unit_stock_qty(frm.doc.name, row.handling_unit, row.s_warehouse)
			handling_units.push({
				row_name: row.name,
				target_row_name: target_row?.name || '',
				item_code: row.item_code,
				item_name: row.item_name,
				handling_unit: row.handling_unit,
				to_handling_unit: target_row?.handling_unit || '',
				remaining_qty: remaining_qty,
				transferred_qty: row.transfer_qty || row.qty,
			})
		}
	}

	return handling_units
}
async function get_handling_unit_stock_qty(name, handling_unit, s_warehouse) {
	let result = await frappe.xcall('beam.beam.overrides.stock_entry.get_handling_unit_qty', {
		voucher_no: name,
		handling_unit: handling_unit,
		warehouse: s_warehouse,
	})
	return flt(result)
}

//re combine
async function set_recombine_handling_units(frm) {
	// const beam_settings = frappe.boot.beam?.settings?.[frm.doc.company]
	// if (!beam_settings?.enable_handling_units) {
	// 	return
	// }
	let to_recombine = await show_handling_unit_recombine_dialog(frm)
	await frappe.xcall('beam.beam.overrides.stock_entry.set_rows_to_recombine', {
		docname: frm.doc.name,
		to_recombine: to_recombine,
	})
}
