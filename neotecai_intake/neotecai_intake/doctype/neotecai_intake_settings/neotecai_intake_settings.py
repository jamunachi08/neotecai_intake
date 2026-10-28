import frappe
from frappe import _
from frappe.model.document import Document


class NeotecAIIntakeSettings(Document):
	def validate(self):
		self.base_url = (self.base_url or "").strip().rstrip("/")
		if self.base_url.endswith("/v1"):
			self.base_url = self.base_url[:-3]
		seen = set()
		for row in self.company_accounts:
			if row.company in seen:
				frappe.throw(_("Company {0} is listed twice").format(row.company))
			seen.add(row.company)
			if frappe.db.get_value("Account", row.vat_account, "company") != row.company:
				frappe.throw(_("Account {0} does not belong to {1}").format(row.vat_account, row.company))
		names = set()
		has_hrms = frappe.db.exists("DocType", "Expense Claim Type")
		for c in self.categories:
			key = (c.category or "").strip().lower()
			if key in names:
				frappe.throw(_("Category {0} is listed twice").format(c.category))
			names.add(key)
			if c.expense_claim_type and has_hrms and not frappe.db.exists("Expense Claim Type", c.expense_claim_type):
				frappe.throw(_("Expense Claim Type {0} not found").format(c.expense_claim_type))
