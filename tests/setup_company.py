import frappe
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete


def run():
	if not frappe.db.exists("Company", "Test KSA Co"):
		setup_complete({
			"language": "English", "country": "Saudi Arabia", "timezone": "Asia/Riyadh",
			"currency": "SAR", "full_name": "Test Admin", "email": "admin@test.local",
			"password": "admin", "company_name": "Test KSA Co", "company_abbr": "TKC",
			"chart_of_accounts": "Standard", "fy_start_date": "2026-01-01", "fy_end_date": "2026-12-31",
			"setup_demo": 0,
		})
		frappe.db.commit()
	print("company", frappe.db.exists("Company", "Test KSA Co"))
	print(frappe.get_all("Account", filters={"company": "Test KSA Co", "account_name": ["like", "%VAT%"]}, pluck="name"))
	print(frappe.get_all("Account", filters={"company": "Test KSA Co", "account_type": ["in", ["Tax"]]}, pluck="name"))
	print(frappe.get_all("Mode of Payment Account", fields=["parent", "default_account"], filters={"company": "Test KSA Co"}))
	print("payable", frappe.db.get_value("Company", "Test KSA Co", ["default_expense_claim_payable_account", "default_payable_account", "cost_center"]))
	print(frappe.get_all("Expense Claim Type", pluck="name"))
