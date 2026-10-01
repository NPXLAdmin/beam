// Copyright (c) 2024, AgriTheory and contributors
// For license information, please see license.txt

frappe.dom.set_style(`
	.barcode-auto-generate-editor input[type="checkbox"]:not(:checked) + .label-area {
		text-decoration: line-through;
		color: var(--text-muted);
	}
`)

frappe.ui.form.on('BEAM Settings', {
	before_save(frm) {
		return confirm_nest_cap_reduction(frm)
	},
	refresh(frm) {
		frm.saved_nest_cap = frm.doc.nest_cap
		const wrapper = $(frm.fields_dict.barcode_exclusions_html.wrapper)
		wrapper.empty()
		wrapper.addClass('barcode-auto-generate-editor').css({
			border: '1px solid var(--border-color)',
			borderRadius: 'var(--border-radius)',
			padding: 'var(--padding-md)',
		})
		frm.barcode_exclusions_editor = new BEAMBarcodeAutoGenerateEditor(wrapper, frm)
	},
})

// Lowering Nest Cap below existing bundles is allowed once acknowledged: the server refuses the
// save until acknowledge_deeper_bundles is set, so ask here and set it on confirmation
function confirm_nest_cap_reduction(frm) {
	if (frm.is_new() || !(frm.doc.nest_cap < (frm.saved_nest_cap || 1))) {
		return
	}
	return frappe
		.xcall('beam.beam.bundle.count_bundles_exceeding', { nest_cap: frm.doc.nest_cap, company: frm.doc.company })
		.then(exceeding => {
			if (!exceeding.count) {
				return
			}
			return new Promise(resolve => {
				frappe.confirm(
					__(
						'{0} existing bundles are deeper than a Nest Cap of {1}; the deepest is at level {2}. They will stay as they are, and only new bundles will be held to the lower cap. Continue?',
						[exceeding.count, frm.doc.nest_cap, exceeding.deepest]
					),
					() => {
						frm.doc.acknowledge_deeper_bundles = 1
						resolve()
					},
					() => {
						frappe.validated = false
						resolve()
					}
				)
			})
		})
}

class BEAMBarcodeAutoGenerateEditor {
	constructor(wrapper, frm) {
		this.wrapper = wrapper
		this.frm = frm
		this.setup()
	}

	get allowed() {
		try {
			return JSON.parse(this.frm.doc.auto_barcode_doctypes || '["Item", "Warehouse"]')
		} catch {
			return ['Item', 'Warehouse']
		}
	}

	setup() {
		this.multicheck = frappe.ui.form.make_control({
			parent: this.wrapper,
			df: {
				fieldname: 'auto_barcode_doctypes',
				fieldtype: 'MultiCheck',
				select_all: true,
				columns: '15rem',
				get_data: () => {
					return frappe
						.xcall('beam.beam.doctype.beam_settings.beam_settings.get_doctypes_with_item_barcodes')
						.then(doctypes => {
							const allowed = this.allowed
							return doctypes.map(dt => ({
								label: __(dt),
								value: dt,
								checked: allowed.includes(dt),
							}))
						})
				},
				on_change: () => {
					this.sync_json()
					this.frm.dirty()
				},
			},
			render_input: true,
		})
	}

	sync_json() {
		const checked = this.multicheck.get_checked_options()
		frappe.model.set_value(this.frm.doctype, this.frm.docname, 'auto_barcode_doctypes', JSON.stringify(checked))
	}
}
