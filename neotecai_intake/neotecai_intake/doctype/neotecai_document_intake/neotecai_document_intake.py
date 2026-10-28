import frappe
from frappe import _
from frappe.model.document import Document

from neotecai_intake.services import files


class NeotecAIDocumentIntake(Document):
	def before_validate(self):
		settings = frappe.get_cached_doc("NeotecAI Intake Settings")
		if not self.create_as:
			self.create_as = settings.default_create_as or "Purchase Invoice"
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
		if self.create_as == "Expense Claim" and not self.employee:
			self.employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")
		if self.expense_claim_doctype and not frappe.db.exists("DocType", self.expense_claim_doctype):
			self.expense_claim_doctype = None
		if not self.expense_claim_doctype and frappe.db.exists("DocType", "Expense Claim"):
			self.expense_claim_doctype = "Expense Claim"
		if not self.mode_of_payment and (self.payment_handling != "Unpaid - pay later" or self.pay_after_approval):
			from neotecai_intake.services.matching import company_accounts

			acc = company_accounts(settings, self.company)
			self.mode_of_payment = acc.default_mode_of_payment if acc else None

	def validate(self):
		if self.source_file and not files.is_supported(self.source_file):
			frappe.throw(_("Upload a PDF or an image (JPG, PNG, WEBP)"))
		if self.create_as == "Expense Claim" and not frappe.db.exists("DocType", "Expense Claim"):
			frappe.throw(_("Expense Claim needs the HRMS app"))
		if not self.is_new() and self.has_value_changed("source_file") and not self.flags.from_extraction:
			if self.purchase_invoice or self.expense_claim:
				frappe.throw(_("Cannot change the file after a document was created"))
			self.status = "Draft"
		for row in self.items:
			if not row.amount and row.rate:
				row.amount = frappe.utils.flt(row.qty or 1) * frappe.utils.flt(row.rate)

	def after_insert(self):
		settings = frappe.get_cached_doc("NeotecAI Intake Settings")
		if settings.enabled and settings.auto_extract and self.source_file:
			from neotecai_intake.services import extract

			extract.enqueue(self.name)
			self.status = "Queued"

	def on_update(self):
		if self.has_value_changed("source_file") and not self.is_new() and self.status == "Draft":
			settings = frappe.get_cached_doc("NeotecAI Intake Settings")
			if settings.enabled and settings.auto_extract:
				from neotecai_intake.services import extract

				extract.enqueue(self.name)

	def on_trash(self):
		if self.purchase_invoice or self.expense_claim:
			frappe.throw(_("Delete the linked Purchase Invoice / Expense Claim first"))
