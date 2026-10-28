frappe.listview_settings["NeotecAI Document Intake"] = {
	add_fields: ["status", "create_as"],
	get_indicator(doc) {
		const colors = {
			Draft: "gray",
			Queued: "blue",
			Processing: "blue",
			"Needs Review": "orange",
			Ready: "green",
			Created: "purple",
			Completed: "green",
			Failed: "red",
			Cancelled: "red",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Upload Invoices / Receipts"), () => bulk_upload(listview));
	},
};

function bulk_upload(listview) {
	const d = new frappe.ui.Dialog({
		title: __("Upload Invoices / Receipts"),
		fields: [
			{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", reqd: 1,
			  default: frappe.defaults.get_user_default("Company") },
			{ fieldname: "create_as", label: __("Create As"), fieldtype: "Select", reqd: 1,
			  options: ["Purchase Invoice", "Expense Claim"], default: "Purchase Invoice" },
			{ fieldname: "employee", label: __("Employee (paid personally)"), fieldtype: "Link", options: "Employee",
			  depends_on: "eval:doc.create_as=='Expense Claim'", mandatory_depends_on: "eval:doc.create_as=='Expense Claim'" },
		],
		primary_action_label: __("Choose Files"),
		primary_action(values) {
			d.hide();
			const urls = [];
			new frappe.ui.FileUploader({
				allow_multiple: true,
				make_attachments_public: false,
				restrictions: { allowed_file_types: [".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"] },
				on_success(file) {
					urls.push(file.file_url);
					clearTimeout(this._t);
					this._t = setTimeout(() => {
						const batch = urls.splice(0, urls.length);
						frappe.call({
							method: "neotecai_intake.api.bulk_upload",
							args: { file_urls: batch, ...values },
							callback: (r) => {
								frappe.show_alert({ message: __("{0} document(s) queued for AI extraction", [(r.message || []).length]), indicator: "green" });
								listview.refresh();
							},
						});
					}, 800);
				},
			});
		},
	});
	d.show();
}
