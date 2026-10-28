frappe.ui.form.on("NeotecAI Intake Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: "neotecai_intake.api.test_connection",
				freeze: true,
				freeze_message: __("Contacting LocalAI..."),
				callback: (r) => {
					const d = r.message || {};
					const models = (d.models || []).map((m) => `<li>${frappe.utils.escape_html(m)}</li>`).join("");
					frappe.msgprint({
						title: d.ok ? __("Success") : __("Problem"),
						indicator: d.ok ? "green" : "red",
						message: `${frappe.utils.escape_html(d.message || "")}${models ? `<br><br>${__("Models on server")}:<ul>${models}</ul>` : ""}`,
					});
				},
			});
		});
	},
});
