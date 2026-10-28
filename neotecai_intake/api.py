"""Whitelisted endpoints used by the desk UI."""

import frappe
from frappe import _

from neotecai_intake.services import create, extract, llm


def _get(name, ptype="write"):
	doc = frappe.get_doc("NeotecAI Document Intake", name)
	doc.check_permission(ptype)
	return doc


@frappe.whitelist()
def run_extraction(name):
	doc = _get(name)
	if doc.purchase_invoice or doc.expense_claim:
		frappe.throw(_("A document was already created from this intake"))
	extract.enqueue(doc.name)
	return "Queued"


@frappe.whitelist()
def create_document(name):
	doc = _get(name)
	if doc.create_as == "Expense Claim":
		target = create.make_expense_claim(doc)
		return {"doctype": "Expense Claim", "name": target}
	target = create.make_purchase_invoice(doc)
	return {"doctype": "Purchase Invoice", "name": target}


@frappe.whitelist()
def create_payment(name):
	doc = _get(name)
	return {"doctype": "Payment Entry", "name": create.make_payment(doc)}


@frappe.whitelist()
def bulk_upload(file_urls, company, create_as="Purchase Invoice", employee=None):
	"""Create one intake per uploaded file."""
	frappe.has_permission("NeotecAI Document Intake", "create", throw=True)
	if isinstance(file_urls, str):
		file_urls = frappe.parse_json(file_urls)
	names = []
	for url in file_urls:
		doc = frappe.get_doc({
			"doctype": "NeotecAI Document Intake",
			"source_file": url,
			"company": company,
			"create_as": create_as,
			"employee": employee,
		}).insert()
		names.append(doc.name)
	return names


@frappe.whitelist()
def test_connection():
	frappe.only_for(("System Manager", "Accounts Manager"))
	settings = frappe.get_doc("NeotecAI Intake Settings")
	try:
		models = llm.list_models(settings)
	except Exception as e:
		return {"ok": False, "message": str(e)}
	found = settings.vision_model in models
	return {
		"ok": found,
		"models": models,
		"message": _("Connected. Model {0} is {1}.").format(
			settings.vision_model, _("installed") if found else _("NOT installed")),
	}
