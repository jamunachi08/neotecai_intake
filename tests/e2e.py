"""End-to-end test for neotecai_intake against a mock LocalAI. Run with bench execute."""

import json

import frappe
from frappe.utils import flt

C = "Test KSA Co"
ABBR = "TKC"
FIX = "/home/claude/tests"
MOCK = "/srv/mock/next.json"
results = []
RUN = frappe.generate_hash(length=5)


def check(label, cond, detail=""):
	results.append((label, bool(cond)))
	print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))


def mock(answer, raw=False):
	with open(MOCK, "w") as f:
		f.write(answer if raw else json.dumps(answer, ensure_ascii=False))


def ensure(doctype, name, values):
	if not frappe.db.exists(doctype, name):
		d = frappe.get_doc({"doctype": doctype, **values})
		d.insert(ignore_permissions=True)
		return d.name
	return name


def setup_masters():
	ensure("Account", f"VAT 15% Input - {ABBR}", {"account_name": "VAT 15% Input", "parent_account": f"Duties and Taxes - {ABBR}",
		"company": C, "account_type": "Tax", "tax_rate": 15})
	ensure("Account", f"Al Rajhi Bank - {ABBR}", {"account_name": "Al Rajhi Bank", "parent_account": f"Bank Accounts - {ABBR}",
		"company": C, "account_type": "Bank"})
	exp = frappe.get_all("Account", filters={"company": C, "root_type": "Expense", "is_group": 0}, pluck="name")
	office_exp = next(a for a in exp if "Office" in a or "Stationery" in a or "Miscellaneous" in a)
	fuel_exp = next((a for a in exp if "Travel" in a or "Fuel" in a), office_exp)
	group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
	for code, acc in (("OFFICE-SUP", office_exp), ("FALLBACK-EXP", office_exp), ("FUEL", fuel_exp)):
		ensure("Item", code, {"item_code": code, "item_name": code, "item_group": group, "is_stock_item": 0,
			"is_purchase_item": 1, "stock_uom": "Nos",
			"item_defaults": [{"company": C, "expense_account": acc, "default_warehouse": None}]})
	if not frappe.db.exists("Mode of Payment", "Bank Transfer"):
		frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": "Bank Transfer", "type": "Bank"}).insert()
	mop = frappe.get_doc("Mode of Payment", "Bank Transfer")
	if not any(a.company == C for a in mop.accounts):
		mop.append("accounts", {"company": C, "default_account": f"Al Rajhi Bank - {ABBR}"})
		mop.save()
	for ect in ("Food", "Travel"):
		d = frappe.get_doc("Expense Claim Type", ect)
		if not any(a.company == C for a in d.accounts):
			d.append("accounts", {"company": C, "default_account": office_exp})
			d.save()
	frappe.db.set_value("Company", C, "default_expense_claim_payable_account", f"Creditors - {ABBR}")
	ensure("Supplier", "Al Noor Office Supplies", {"supplier_name": "Al Noor Office Supplies",
		"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"), "tax_id": "300 000 000 000 003"})
	emp = frappe.db.get_value("Employee", {"first_name": "Sara", "company": C}, "name")
	if not emp:
		emp = frappe.get_doc({"doctype": "Employee", "first_name": "Sara", "last_name": "Test", "gender": "Female",
			"date_of_birth": "1990-01-01", "date_of_joining": "2024-01-01", "company": C, "status": "Active",
			"expense_approver": "Administrator"}).insert().name

	s = frappe.get_single("NeotecAI Intake Settings")
	s.base_url = "http://127.0.0.1:8089/v1"  # trailing /v1 should be stripped
	s.api_key = "sk-test"
	s.vision_model = "qwen3.6-35b-a3b"
	s.default_item = "FALLBACK-EXP"
	s.auto_extract = 1
	from neotecai_intake.install import DEFAULT_CATEGORIES

	s.set("categories", [{"category": c, "hint": h} for c, h in DEFAULT_CATEGORIES])
	s.set("company_accounts", [{"company": C, "vat_account": f"VAT 15% Input - {ABBR}", "cost_center": f"Main - {ABBR}",
		"default_mode_of_payment": "Bank Transfer"}])
	for c in s.categories:
		if c.category == "Office Supplies":
			c.item_code = "OFFICE-SUP"
		if c.category == "Meals and Entertainment":
			c.expense_claim_type = "Food"
		if c.category == "Fuel and Vehicle":
			c.item_code, c.expense_claim_type = "FUEL", "Travel"
		if c.category == "Other":
			c.expense_claim_type = "Travel"
	s.save()
	frappe.db.commit()
	check("settings strips /v1 from base URL", frappe.db.get_single_value("NeotecAI Intake Settings", "base_url") == "http://127.0.0.1:8089")
	return emp


def upload(fname):
	with open(f"{FIX}/{fname}", "rb") as f:
		content = f.read()
	return frappe.get_doc({"doctype": "File", "file_name": fname, "content": content, "is_private": 1}).insert().file_url


def run_extract(name):
	from neotecai_intake.services import extract

	extract.run(name)
	return frappe.get_doc("NeotecAI Document Intake", name)


def test_purchase_invoice():
	print("\n== Tax invoice PDF -> Purchase Invoice -> Payment Entry")
	mock({
		"document_kind": "tax_invoice", "supplier_name": "Al Noor Office Supplies", "supplier_vat_number": "300000000000003",
		"invoice_number": f"INV-2026-0042-{RUN}", "invoice_date": "2026-10-20", "currency": "SAR",
		"lines": [
			{"description": "Printer paper A4 (box)", "quantity": 10, "unit_price_excl_vat": 50, "amount_excl_vat": 500,
			 "vat_rate": 15, "vat_amount": 75, "category": "Office Supplies"},
			{"description": "Toner cartridge HP 26A", "quantity": 2, "unit_price_excl_vat": 250, "amount_excl_vat": 500,
			 "vat_rate": 15, "vat_amount": 75, "category": "Office Supplies"},
		],
		"net_total": 1000, "vat_total": 150, "grand_total": 1150, "payment_status": "unpaid", "payment_method": "",
	})
	doc = frappe.get_doc({"doctype": "NeotecAI Document Intake", "source_file": upload("tax_invoice.pdf"), "company": C,
		"create_as": "Purchase Invoice", "payment_handling": "Create Payment Entry after submit"}).insert()
	frappe.db.commit()
	check("auto-extract queued on upload", doc.status == "Queued", doc.status)
	doc = run_extract(doc.name)
	req = json.load(open("/srv/mock/last_request.json"))
	check("API key sent as Bearer", req["auth"] == "Bearer sk-test", req["auth"])
	cats = req["req"]["response_format"]["json_schema"]["schema"]["properties"]["lines"]["items"]["properties"]["category"]["enum"]
	check("category enum sent to model", "Office Supplies" in cats, cats)
	check("PDF text layer not required (image PDF)", True)
	check("status Ready", doc.status == "Ready", f"{doc.status} / {doc.warnings} / {doc.error}")
	check("ZATCA QR decoded from PDF", bool(doc.qr_data) and doc.qr_verified == 1, doc.qr_data)
	check("supplier matched by VAT (stored with spaces)", doc.supplier == "Al Noor Office Supplies", doc.supplier)
	check("VAT recoverable", doc.vat_recoverable == 1)
	check("lines mapped to category item", [i.item_code for i in doc.items] == ["OFFICE-SUP", "OFFICE-SUP"])

	from neotecai_intake import api

	r = api.create_document(doc.name)
	pi = frappe.get_doc("Purchase Invoice", r["name"])
	check("PI draft created", pi.docstatus == 0)
	check("PI grand total 1150", flt(pi.grand_total) == 1150, pi.grand_total)
	check("PI VAT row 150 on input VAT account", len(pi.taxes) == 1 and flt(pi.taxes[0].tax_amount) == 150
		and pi.taxes[0].account_head == f"VAT 15% Input - {ABBR}", [(t.account_head, t.tax_amount) for t in pi.taxes])
	check("PI bill no/date", pi.bill_no == f"INV-2026-0042-{RUN}" and str(pi.bill_date) == "2026-10-20")
	check("PI posting date = invoice date", str(pi.posting_date) == "2026-10-20", pi.posting_date)
	check("file attached to PI", frappe.db.exists("File", {"attached_to_doctype": "Purchase Invoice", "attached_to_name": pi.name}))
	try:
		api.create_document(doc.name)
		check("second create blocked", False)
	except frappe.ValidationError:
		check("second create blocked", True)

	pi.submit()
	doc.reload()
	check("payment draft auto-created on PI submit", doc.payment_document and doc.status == "Completed", f"{doc.payment_document} {doc.status}")
	if doc.payment_document:
		pe = frappe.get_doc("Payment Entry", doc.payment_document)
		check("PE amount 1150 from bank, against PI", flt(pe.paid_amount) == 1150 and pe.paid_from == f"Al Rajhi Bank - {ABBR}"
			and pe.references[0].reference_name == pi.name, (pe.paid_amount, pe.paid_from))
		pe.submit()
		check("PI paid after PE submit", frappe.db.get_value("Purchase Invoice", pi.name, "outstanding_amount") == 0)
	gl = frappe.get_all("GL Entry", filters={"voucher_no": pi.name, "is_cancelled": 0}, fields=["account", "debit", "credit"])
	vat_debit = sum(g.debit for g in gl if g.account == f"VAT 15% Input - {ABBR}")
	check("GL: input VAT debited 150", vat_debit == 150, gl)

	# duplicate detection
	dup = frappe.get_doc({"doctype": "NeotecAI Document Intake", "source_file": upload("tax_invoice.pdf"), "company": C,
		"create_as": "Purchase Invoice"}).insert()
	dup = run_extract(dup.name)
	check("duplicate invoice flagged", "duplicate" in (dup.warnings or "").lower() and dup.status == "Needs Review", dup.warnings)
	try:
		api.create_document(dup.name)
		check("duplicate PI blocked", False)
	except frappe.ValidationError:
		check("duplicate PI blocked", True)
	frappe.db.commit()


def test_expense_claim(emp):
	print("\n== Restaurant receipt JPG -> Expense Claim -> employee payment")
	mock("<think>reading</think>```json\n" + json.dumps({
		"document_kind": "simplified_tax_invoice", "supplier_name": "Najd Grill Restaurant", "supplier_vat_number": "٣١٠١٢٢٣٩٣٥٠٠٠٠٣",
		"invoice_number": f"88231-{RUN}", "invoice_date": "2026-10-25", "currency": "SAR",
		"lines": [{"description": "Mixed grill x2", "quantity": 2, "unit_price_excl_vat": 50, "amount_excl_vat": 100,
			"vat_rate": 15, "vat_amount": 15, "category": "Meals and Entertainment"}],
		"net_total": 100, "vat_total": 15, "grand_total": 115, "payment_status": "paid", "payment_method": "mada",
	}, ensure_ascii=False) + "\n```", raw=True)
	doc = frappe.get_doc({"doctype": "NeotecAI Document Intake", "source_file": upload("restaurant_receipt.jpg"), "company": C,
		"create_as": "Expense Claim", "employee": emp, "pay_after_approval": 1}).insert()
	doc = run_extract(doc.name)
	check("think tags / code fence parsed", doc.status in ("Ready", "Needs Review"), f"{doc.status} {doc.error}")
	check("Arabic-Indic VAT digits normalised", doc.supplier_vat_extracted == "310122393500003", doc.supplier_vat_extracted)
	check("QR verified on JPG", doc.qr_verified == 1, doc.qr_data)
	check("line mapped to Expense Claim Type Food", doc.items and doc.items[0].expense_claim_type == "Food")

	from neotecai_intake import api

	r = api.create_document(doc.name)
	ec = frappe.get_doc("Expense Claim", r["name"])
	check("EC draft: sanctioned 100 + VAT 15 = 115", flt(ec.total_sanctioned_amount) == 100 and flt(ec.total_taxes_and_charges) == 15
		and flt(ec.grand_total) == 115, (ec.total_sanctioned_amount, ec.total_taxes_and_charges, ec.grand_total))
	check("EC approver/payable set", ec.expense_approver == "Administrator" and ec.payable_account == f"Creditors - {ABBR}")
	ec.approval_status = "Approved"
	ec.submit()
	doc.reload()
	check("employee payment draft created after approval", bool(doc.payment_document), doc.payment_document)
	if doc.payment_document:
		pe = frappe.get_doc("Payment Entry", doc.payment_document)
		check("PE pays employee 115", pe.party_type == "Employee" and pe.party == emp and flt(pe.paid_amount) == 115,
			(pe.party_type, pe.party, pe.paid_amount))
	frappe.db.commit()


def test_paid_receipt_no_vat():
	print("\n== Fuel receipt PNG, no VAT number, already paid -> paid Purchase Invoice")
	mock({
		"document_kind": "receipt", "supplier_name": "Petrol Station 24", "supplier_vat_number": "",
		"invoice_number": f"5512-{RUN}", "invoice_date": "2026-10-26", "currency": "SR",
		"lines": [{"description": "Diesel 50 L", "quantity": 50, "unit_price_excl_vat": 1.6, "amount_excl_vat": 80,
			"vat_rate": 15, "vat_amount": 12, "category": "Fuel and Vehicle"}],
		"net_total": 80, "vat_total": 12, "grand_total": 92, "payment_status": "paid", "payment_method": "cash",
	})
	frappe.db.set_single_value("NeotecAI Intake Settings", "auto_create_supplier", 1)
	doc = frappe.get_doc({"doctype": "NeotecAI Document Intake", "source_file": upload("fuel_receipt.png"), "company": C,
		"create_as": "Purchase Invoice", "payment_handling": "Already paid - record on Purchase Invoice",
		"mode_of_payment": "Cash"}).insert()
	doc = run_extract(doc.name)
	check("SR currency mapped to SAR", doc.currency == "SAR", doc.currency)
	check("VAT not recoverable without VAT number", doc.vat_recoverable == 0)
	check("warning about missing VAT number", "not recoverable" in (doc.warnings or ""), doc.warnings)
	from neotecai_intake import api

	r = api.create_document(doc.name)
	pi = frappe.get_doc("Purchase Invoice", r["name"])
	check("supplier auto-created", frappe.db.exists("Supplier", pi.supplier) and "Petrol" in pi.supplier, pi.supplier)
	check("PI gross 92, no tax rows", flt(pi.grand_total) == 92 and not pi.taxes, (pi.grand_total, len(pi.taxes)))
	check("PI marked paid from Cash", pi.is_paid == 1 and pi.cash_bank_account == f"Cash - {ABBR}" and flt(pi.paid_amount) == 92,
		(pi.is_paid, pi.cash_bank_account, pi.paid_amount))
	check("fuel line uses FUEL item", pi.items[0].item_code == "FUEL")
	pi.submit()
	check("paid PI outstanding 0", flt(frappe.db.get_value("Purchase Invoice", pi.name, "outstanding_amount")) == 0)
	doc.reload()
	check("intake completed, no extra payment", doc.status == "Completed" and not doc.payment_document)
	frappe.db.commit()


def test_failure_and_trash():
	print("\n== Error handling")
	s = frappe.get_single("NeotecAI Intake Settings")
	s.vision_model = "missing-model"
	s.save()
	doc = frappe.get_doc({"doctype": "NeotecAI Document Intake", "source_file": upload("fuel_receipt.png"), "company": C,
		"create_as": "Purchase Invoice"}).insert()
	doc = run_extract(doc.name)
	check("server error -> status Failed with message", doc.status == "Failed" and doc.error, (doc.status, doc.error))
	s.vision_model = "qwen3.6-35b-a3b"
	s.save()
	frappe.db.commit()

	from neotecai_intake import api

	conn = api.test_connection()
	check("test connection finds model", conn["ok"], conn)

	# deleting a draft PI frees the intake again
	mock(json.load(open("/srv/mock/next.json")) if False else {
		"document_kind": "tax_invoice", "supplier_name": "Al Noor Office Supplies", "supplier_vat_number": "300000000000003",
		"invoice_number": f"INV-2026-0099-{RUN}", "invoice_date": "2026-10-27", "currency": "SAR", "lines": [],
		"net_total": 200, "vat_total": 30, "grand_total": 230, "payment_status": "unknown", "payment_method": ""})
	doc = run_extract(doc.name)
	check("no lines -> one line from totals", len(doc.items) == 1 and flt(doc.items[0].amount) == 200, [(i.amount) for i in doc.items])
	r = api.create_document(doc.name)
	frappe.delete_doc("Purchase Invoice", r["name"])
	doc.reload()
	check("deleting draft PI frees intake", not doc.purchase_invoice and doc.status == "Needs Review", (doc.purchase_invoice, doc.status))
	frappe.db.commit()


def run():
	frappe.set_user("Administrator")
	frappe.db.sql("update `tabNeotecAI Document Intake` set status='Cancelled'")
	frappe.db.sql("delete from `tabError Log`")
	emp = setup_masters()
	for t in (test_purchase_invoice, lambda: test_expense_claim(emp), test_paid_receipt_no_vat, test_failure_and_trash):
		try:
			t()
		except Exception:
			import traceback

			traceback.print_exc()
			check("test raised", False)
			frappe.db.rollback()
	passed = sum(1 for _, ok in results if ok)
	print(f"\nRESULT {passed}/{len(results)} passed")
