"""Document event hooks."""

import frappe
from frappe import _

from neotecai_intake.services import create, files


def _intake_for(field, name):
	return frappe.db.get_value("NeotecAI Document Intake", {field: name}, "name")


def on_purchase_invoice_submit(doc, method=None):
	name = _intake_for("purchase_invoice", doc.name)
	if not name:
		return
	intake = frappe.get_doc("NeotecAI Document Intake", name)
	status = "Completed"
	if intake.payment_handling == "Create Payment Entry after submit" and not doc.is_paid:
		status = _try_payment(intake) or status
	intake.db_set("status", status)


def on_expense_claim_submit(doc, method=None):
	name = _intake_for("expense_claim", doc.name)
	if not name:
		return
	intake = frappe.get_doc("NeotecAI Document Intake", name)
	status = "Completed"
	if intake.pay_after_approval and doc.approval_status == "Approved":
		status = _try_payment(intake) or status
	intake.db_set("status", status)


def _try_payment(intake):
	"""Never block the submission of the invoice/claim because of the payment draft."""
	frappe.db.savepoint("neotecai_intake_payment")
	try:
		pe = create.make_payment(intake)
		frappe.msgprint(_("Draft Payment Entry {0} created").format(
			frappe.utils.get_link_to_form("Payment Entry", pe)), alert=True, indicator="green")
	except Exception as e:
		frappe.db.rollback(save_point="neotecai_intake_payment")
		frappe.log_error(f"NeotecAI Intake: payment draft failed for {intake.name}")
		frappe.msgprint(_("Could not create the payment draft: {0}").format(e), indicator="orange", alert=True)
		return "Completed"


def on_target_cancel(doc, method=None):
	field = "purchase_invoice" if doc.doctype == "Purchase Invoice" else "expense_claim"
	name = _intake_for(field, doc.name)
	if name:
		frappe.db.set_value("NeotecAI Document Intake", name, "status", "Cancelled")


def on_target_trash(doc, method=None):
	"""Deleting a draft PI/EC frees the intake so it can be created again."""
	field = "purchase_invoice" if doc.doctype == "Purchase Invoice" else "expense_claim"
	name = _intake_for(field, doc.name)
	if name:
		values = {field: None, "status": "Needs Review"}
		if field == "expense_claim":
			values["expense_claim_doctype"] = "Expense Claim"
		frappe.db.set_value("NeotecAI Document Intake", name, values)


def on_file_after_insert(doc, method=None):
	"""Turn attachments received on the intake mailbox into intake documents."""
	if doc.attached_to_doctype != "Communication" or not doc.attached_to_name:
		return
	if not files.is_supported(doc.file_name or doc.file_url):
		return
	settings = frappe.get_cached_doc("NeotecAI Intake Settings")
	if not (settings.enabled and settings.intake_email_account):
		return
	comm = frappe.db.get_value("Communication", doc.attached_to_name,
		["email_account", "sent_or_received"], as_dict=True)
	if not comm or comm.email_account != settings.intake_email_account or comm.sent_or_received != "Received":
		return
	company = settings.email_company or frappe.defaults.get_global_default("company")
	if not company:
		return
	if (doc.file_size or 0) < 8000 and not (doc.file_name or "").lower().endswith(".pdf"):
		return  # skip logos / signature images
	intake = frappe.get_doc({
		"doctype": "NeotecAI Document Intake",
		"source_file": doc.file_url,
		"company": company,
		"create_as": "Purchase Invoice",
		"source_email": doc.attached_to_name,
	})
	intake.insert(ignore_permissions=True)
