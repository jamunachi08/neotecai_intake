frappe.ui.form.on("NeotecAI Document Intake", {
	setup(frm) {
		frm.set_query("item_code", "items", () => ({ filters: { disabled: 0, is_purchase_item: 1 } }));
		frm.set_query("employee", () => ({ filters: { company: frm.doc.company, status: "Active" } }));
	},

	onload(frm) {
		frappe.realtime.on("neotecai_intake_done", (data) => {
			if (data && data.name === frm.doc.name) frm.reload_doc();
		});
	},

	refresh(frm) {
		render_preview(frm);
		show_warnings(frm);
		if (frm.is_new()) return;

		const created = frm.doc.purchase_invoice || frm.doc.expense_claim;
		const busy = ["Queued", "Processing"].includes(frm.doc.status);

		if (busy) {
			frm.dashboard.set_headline(__("The AI is reading this document. The form refreshes when it finishes."), "blue");
		}

		if (!created && !busy && frm.doc.source_file) {
			const label = frm.doc.status === "Draft" || frm.doc.status === "Failed" ? __("Extract with AI") : __("Re-extract");
			frm.add_custom_button(label, () => {
				frappe.call({
					method: "neotecai_intake.api.run_extraction",
					args: { name: frm.doc.name },
					callback: () => frm.reload_doc(),
				});
			});
		}

		if (!created && ["Needs Review", "Ready"].includes(frm.doc.status)) {
			const btn = frm.add_custom_button(__("Create {0}", [__(frm.doc.create_as)]), () => {
				const go = () =>
					frm.save().then(() =>
						frappe.call({
							method: "neotecai_intake.api.create_document",
							args: { name: frm.doc.name },
							freeze: true,
							callback: (r) => {
								if (r.message) frappe.set_route("Form", r.message.doctype, r.message.name);
							},
						})
					);
				if (frm.doc.warnings) {
					frappe.confirm(__("There are warnings on this document. Did you review them?"), go);
				} else {
					go();
				}
			});
			btn.addClass("btn-primary");
		}

		if (created && !frm.doc.payment_document) {
			frm.add_custom_button(__("Create Payment"), () => {
				frappe.call({
					method: "neotecai_intake.api.create_payment",
					args: { name: frm.doc.name },
					freeze: true,
					callback: (r) => r.message && frappe.set_route("Form", r.message.doctype, r.message.name),
				});
			}, __("Create"));
		}
	},

	create_as(frm) {
		if (frm.doc.create_as === "Expense Claim") frm.set_value("payment_handling", "Unpaid - pay later");
	},

	source_file(frm) {
		render_preview(frm);
	},
});

frappe.ui.form.on("NeotecAI Document Intake Item", {
	qty: recalc_row,
	rate: recalc_row,
});

function recalc_row(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "amount", flt(row.qty || 1) * flt(row.rate));
}

function render_preview(frm) {
	const wrapper = frm.get_field("preview_html").$wrapper;
	const url = frm.doc.source_file;
	if (!url) {
		wrapper.html("");
		return;
	}
	const safe = encodeURI(url);
	if (url.toLowerCase().endsWith(".pdf")) {
		wrapper.html(`<iframe src="${safe}#view=FitH" style="width:100%;height:640px;border:1px solid var(--border-color);border-radius:8px"></iframe>`);
	} else {
		wrapper.html(`<a href="${safe}" target="_blank"><img src="${safe}" style="max-width:100%;max-height:640px;border:1px solid var(--border-color);border-radius:8px"></a>`);
	}
}

function show_warnings(frm) {
	if (frm.doc.warnings && ["Needs Review", "Ready"].includes(frm.doc.status)) {
		frm.dashboard.add_comment(
			`<b>${__("Please check")}:</b><br>${frappe.utils.escape_html(frm.doc.warnings).replace(/\n/g, "<br>")}`,
			"orange",
			true
		);
	}
	if (frm.doc.status === "Failed" && frm.doc.error) {
		frm.dashboard.add_comment(frappe.utils.escape_html(frm.doc.error), "red", true);
	}
}
