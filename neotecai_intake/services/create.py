"""Build draft ERPNext documents from a reviewed NeotecAI Document Intake."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from neotecai_intake.services.extract import duplicate_warnings
from neotecai_intake.services.matching import company_accounts


def _settings():
	return frappe.get_cached_doc("NeotecAI Intake Settings")


def _check_ready(doc):
	if doc.status in ("Draft", "Queued", "Processing"):
		frappe.throw(_("Run the extraction first"))
	if doc.purchase_invoice or doc.expense_claim:
		frappe.throw(_("A document was already created from this intake"))
	if not doc.items:
		frappe.throw(_("Add at least one line"))
	if flt(doc.grand_total) <= 0:
		frappe.throw(_("Grand Total must be greater than zero"))


def _gross_lines(doc):
	"""Return [(row, net, vat)] with VAT spread so lines sum exactly to the document totals."""
	rows = list(doc.items)
	net_sum = sum(flt(r.amount) for r in rows) or 1
	vat_total = flt(doc.vat_total)
	explicit = sum(flt(r.vat_amount) for r in rows)
	out, allocated = [], 0.0
	for idx, r in enumerate(rows):
		if abs(explicit - vat_total) <= 0.05 and explicit:
			vat = flt(r.vat_amount)
		else:
			vat = flt(vat_total * flt(r.amount) / net_sum, 2)
		if idx == len(rows) - 1:
			vat = flt(vat_total - allocated, 2)
		allocated += vat
		out.append((r, flt(r.amount, 2), vat))
	return out


def _qty_rate(row, amount):
	qty = flt(row.qty) or 1
	rate = flt(amount / qty, 6)
	if abs(flt(rate * qty, 2) - flt(amount, 2)) > 0.005:
		return 1, flt(amount, 2)
	return qty, rate


def _attach(doc, target_doctype, target_name):
	try:
		frappe.get_doc({
			"doctype": "File",
			"file_url": doc.source_file,
			"attached_to_doctype": target_doctype,
			"attached_to_name": target_name,
			"is_private": 1 if (doc.source_file or "").startswith("/private/") else 0,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error("NeotecAI Intake: could not attach file")


# ------------------------------------------------------------------ supplier

def ensure_supplier(doc, settings):
	if doc.supplier:
		return doc.supplier
	if not settings.auto_create_supplier:
		frappe.throw(_("Select a Supplier first"))
	if not doc.supplier_name_extracted:
		frappe.throw(_("Supplier name is empty"))
	acc = company_accounts(settings, doc.company)
	group = (acc and acc.supplier_group) or frappe.db.get_single_value("Buying Settings", "supplier_group") \
		or frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	supplier = frappe.get_doc({
		"doctype": "Supplier",
		"supplier_name": doc.supplier_name_extracted,
		"supplier_group": group,
		"supplier_type": "Company",
		"tax_id": doc.supplier_vat_extracted or None,
		"country": frappe.get_cached_value("Company", doc.company, "country"),
	}).insert()
	doc.supplier = supplier.name
	return supplier.name


# ------------------------------------------------------------------ Purchase Invoice

def make_purchase_invoice(doc):
	_check_ready(doc)
	frappe.has_permission("Purchase Invoice", "create", throw=True)
	settings = _settings()
	supplier = ensure_supplier(doc, settings)
	dup = [w for w in duplicate_warnings(doc) if "Purchase Invoice" in w]
	if dup:
		frappe.throw(dup[0], title=_("Duplicate"))

	acc = company_accounts(settings, doc.company)
	if doc.vat_recoverable and flt(doc.vat_total) and not (acc and acc.vat_account):
		frappe.throw(_("Set the Input VAT Account for {0} in NeotecAI Intake Settings").format(doc.company))

	pi = frappe.new_doc("Purchase Invoice")
	pi.company = doc.company
	pi.supplier = supplier
	pi.currency = doc.currency
	pi.bill_no = doc.bill_no
	pi.bill_date = doc.bill_date
	if settings.post_on_invoice_date and doc.bill_date:
		pi.set_posting_time = 1
		pi.posting_date = doc.bill_date
	pi.disable_rounded_total = 1
	pi.remarks = _("Created from {0} ({1})").format(doc.name, doc.document_kind or "")
	cost_center = acc.cost_center if acc else None
	if cost_center:
		pi.cost_center = cost_center

	for row, net, vat in _gross_lines(doc):
		amount = net if doc.vat_recoverable else flt(net + vat, 2)
		item_code = row.item_code or settings.default_item
		if not item_code:
			frappe.throw(_("Row {0}: no Item. Map the category to an Item or set a Fallback Item in NeotecAI Intake Settings").format(row.idx))
		qty, rate = _qty_rate(row, amount)
		pi.append("items", {
			"item_code": item_code,
			"description": row.description,
			"qty": qty,
			"rate": rate,
			"cost_center": cost_center,
		})

	if doc.vat_recoverable and flt(doc.vat_total):
		pi.append("taxes", {
			"category": "Total",
			"add_deduct_tax": "Add",
			"charge_type": "Actual",
			"account_head": acc.vat_account,
			"description": _("Input VAT ({0})").format(doc.supplier_vat_extracted or ""),
			"tax_amount": flt(doc.vat_total, 2),
			"cost_center": cost_center,
		})

	pi.set_missing_values()
	# set_missing_values may pull the supplier's default tax template; keep only our VAT row
	pi.taxes_and_charges = None
	pi.set("taxes", [t for t in pi.taxes if t.charge_type == "Actual" and t.account_head == (acc.vat_account if acc else None)])
	pi.calculate_taxes_and_totals()

	if doc.payment_handling == "Already paid - record on Purchase Invoice":
		mop = doc.mode_of_payment or (acc.default_mode_of_payment if acc else None)
		if not mop:
			frappe.throw(_("Select a Mode of Payment"))
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

		pi.is_paid = 1
		pi.mode_of_payment = mop
		pi.cash_bank_account = get_bank_cash_account(mop, doc.company)["account"]
		pi.paid_amount = pi.grand_total
		pi.base_paid_amount = pi.base_grand_total
		pi.calculate_taxes_and_totals()

	pi.insert()
	diff = abs(flt(pi.grand_total) - flt(doc.grand_total))
	if diff > 0.05:
		frappe.msgprint(_("Purchase Invoice total {0} differs from the document total {1}. Please check.").format(
			pi.grand_total, doc.grand_total), indicator="orange", alert=True)

	_attach(doc, "Purchase Invoice", pi.name)
	doc.db_set({"purchase_invoice": pi.name, "status": "Created", "supplier": supplier})
	return pi.name


# ------------------------------------------------------------------ Expense Claim

def make_expense_claim(doc):
	_check_ready(doc)
	if not frappe.db.exists("DocType", "Expense Claim"):
		frappe.throw(_("Expense Claim needs the HRMS app"))
	frappe.has_permission("Expense Claim", "create", throw=True)
	if not doc.employee:
		frappe.throw(_("Select the Employee who paid"))
	settings = _settings()
	acc = company_accounts(settings, doc.company)
	if doc.vat_recoverable and flt(doc.vat_total) and not (acc and acc.vat_account):
		frappe.throw(_("Set the Input VAT Account for {0} in NeotecAI Intake Settings").format(doc.company))

	emp = frappe.db.get_value("Employee", doc.employee, ["company", "expense_approver", "department"], as_dict=True)
	if emp.company != doc.company:
		frappe.throw(_("Employee {0} belongs to {1}").format(doc.employee, emp.company))

	company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	if doc.currency and doc.currency != company_currency:
		frappe.throw(_("Expense Claims are in {0}; this document is in {1}. Convert the amounts first.").format(
			company_currency, doc.currency))

	ec = frappe.new_doc("Expense Claim")
	ec.employee = doc.employee
	ec.company = doc.company
	ec.posting_date = nowdate()
	ec.expense_approver = emp.expense_approver
	ec.payable_account = frappe.get_cached_value("Company", doc.company, "default_expense_claim_payable_account")
	ec.remark = _("Created from {0}: {1} {2}").format(doc.name, doc.supplier_name_extracted or "", doc.bill_no or "")
	cost_center = acc.cost_center if acc else None
	if cost_center:
		ec.cost_center = cost_center

	default_type = next((c.expense_claim_type for c in settings.categories if c.expense_claim_type), None)
	for row, net, vat in _gross_lines(doc):
		amount = net if doc.vat_recoverable else flt(net + vat, 2)
		etype = row.expense_claim_type or default_type
		if not etype or not frappe.db.exists("Expense Claim Type", etype):
			frappe.throw(_("Row {0}: set a valid Expense Claim Type (category mapping in NeotecAI Intake Settings)").format(row.idx))
		ec.append("expenses", {
			"expense_date": doc.bill_date or nowdate(),
			"expense_type": etype,
			"description": f"{doc.supplier_name_extracted or ''} {doc.bill_no or ''}: {row.description}".strip(),
			"amount": amount,
			"sanctioned_amount": amount,
			"cost_center": cost_center,
		})

	if doc.vat_recoverable and flt(doc.vat_total):
		ec.append("taxes", {
			"account_head": acc.vat_account,
			"rate": 0,
			"tax_amount": flt(doc.vat_total, 2),
			"description": _("Input VAT ({0})").format(doc.supplier_vat_extracted or ""),
			"cost_center": cost_center,
		})

	ec.insert()
	_attach(doc, "Expense Claim", ec.name)
	doc.db_set({"expense_claim_doctype": "Expense Claim", "expense_claim": ec.name, "status": "Created"})
	return ec.name


# ------------------------------------------------------------------ Payments

def make_payment(doc):
	"""Draft payment against the submitted Purchase Invoice / approved Expense Claim."""
	if doc.payment_document:
		return doc.payment_document
	settings = _settings()
	acc = company_accounts(settings, doc.company)
	mop = doc.mode_of_payment or (acc.default_mode_of_payment if acc else None)
	bank_account = None
	if mop:
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

		bank_account = get_bank_cash_account(mop, doc.company)["account"]

	if doc.purchase_invoice:
		pi = frappe.get_doc("Purchase Invoice", doc.purchase_invoice)
		if pi.docstatus != 1:
			frappe.throw(_("Submit Purchase Invoice {0} first").format(pi.name))
		if pi.is_paid or flt(pi.outstanding_amount) <= 0:
			frappe.throw(_("Purchase Invoice {0} is already paid").format(pi.name))
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry("Purchase Invoice", pi.name, bank_account=bank_account)
	elif doc.expense_claim:
		ec = frappe.get_doc("Expense Claim", doc.expense_claim)
		if ec.docstatus != 1 or ec.approval_status != "Approved":
			frappe.throw(_("Expense Claim {0} must be approved and submitted first").format(ec.name))
		from hrms.overrides.employee_payment_entry import get_payment_entry_for_employee

		pe = get_payment_entry_for_employee("Expense Claim", ec.name, bank_account=bank_account)
	else:
		frappe.throw(_("Create the Purchase Invoice or Expense Claim first"))

	if mop:
		pe.mode_of_payment = mop
	pe.reference_no = pe.reference_no or (doc.bill_no or doc.name)
	pe.reference_date = pe.reference_date or nowdate()
	pe.remarks = _("Payment for {0} (NeotecAI Intake {1})").format(doc.bill_no or "", doc.name)
	pe.insert()
	doc.db_set({"payment_doctype": "Payment Entry", "payment_document": pe.name})
	return pe.name
