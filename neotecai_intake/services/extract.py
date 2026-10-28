"""Extraction job: file -> LocalAI vision model -> reviewed fields on NeotecAI Document Intake."""

import json
import time

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

from neotecai_intake.services import files, llm
from neotecai_intake.services.matching import (
	category_map,
	digits_only,
	find_supplier,
	is_valid_ksa_vat,
)

KIND_MAP = {
	"tax_invoice": "Tax Invoice",
	"simplified_tax_invoice": "Simplified Tax Invoice",
	"receipt": "Receipt",
	"credit_note": "Credit Note",
	"other": "Other",
}

SYSTEM_PROMPT = """You read supplier invoices, bills and receipts (often from Saudi Arabia, in Arabic, English or both) and return their data as JSON.

Rules:
- Read only what is printed. Never invent values. Use "" for unknown text and 0 for unknown numbers.
- Convert Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) to Western digits. Use "." as decimal separator, no thousands separators.
- invoice_date as YYYY-MM-DD (Gregorian). If only a Hijri date is printed, convert it to Gregorian.
- supplier = the SELLER who issued the document (not the buyer). supplier_vat_number = the seller's VAT / tax number (الرقم الضريبي), digits only.
- document_kind: "tax_invoice" (فاتورة ضريبية, has buyer details), "simplified_tax_invoice" (فاتورة ضريبية مبسطة), "receipt" (till slip without tax invoice title), "credit_note" (إشعار دائن), else "other".
- lines: one entry per printed line item. amount_excl_vat is the line total BEFORE VAT. If the document prints prices including VAT, back-calculate the amount before VAT using the VAT rate. If there are more than 25 lines, group them by category.
- category: choose the closest from the allowed list for each line.
- net_total = total before VAT, vat_total = total VAT, grand_total = amount payable including VAT.
- payment_status: "paid" if the document shows it was paid (cash/card/mada receipt, "paid", "مدفوع"), "unpaid" if it shows a due date or credit terms, otherwise "unknown".
"""


def build_schema(categories):
	cat = {"type": "string"}
	if categories:
		cat["enum"] = categories
	line = {
		"type": "object",
		"properties": {
			"description": {"type": "string"},
			"quantity": {"type": "number"},
			"unit_price_excl_vat": {"type": "number"},
			"amount_excl_vat": {"type": "number"},
			"vat_rate": {"type": "number"},
			"vat_amount": {"type": "number"},
			"category": cat,
		},
		"required": ["description", "quantity", "unit_price_excl_vat", "amount_excl_vat", "vat_rate",
			"vat_amount", "category"],
	}
	props = {
		"document_kind": {"type": "string", "enum": list(KIND_MAP)},
		"supplier_name": {"type": "string"},
		"supplier_vat_number": {"type": "string"},
		"invoice_number": {"type": "string"},
		"invoice_date": {"type": "string"},
		"currency": {"type": "string"},
		"lines": {"type": "array", "items": line},
		"net_total": {"type": "number"},
		"vat_total": {"type": "number"},
		"grand_total": {"type": "number"},
		"payment_status": {"type": "string", "enum": ["paid", "unpaid", "unknown"]},
		"payment_method": {"type": "string"},
	}
	return {"type": "object", "properties": props, "required": list(props)}


def build_messages(prepared, categories, category_hints):
	cat_text = "\n".join(f"- {c}: {h}" if h else f"- {c}" for c, h in zip(categories, category_hints))
	user_text = "Extract this document.\nAllowed categories:\n" + (cat_text or "- Other")
	if prepared.get("text"):
		user_text += (
			"\n\nText layer extracted from the PDF (may be incomplete or out of order; "
			"trust the image when they disagree):\n" + prepared["text"][:6000]
		)
	content = [{"type": "text", "text": user_text}]
	for img in prepared["images"]:
		content.append({"type": "image_url", "image_url": {"url": files.to_data_url(img)}})
	return [
		{"role": "system", "content": SYSTEM_PROMPT},
		{"role": "user", "content": content},
	]


# --------------------------------------------------------------------------- job

def enqueue(intake_name):
	frappe.db.set_value("NeotecAI Document Intake", intake_name, {"status": "Queued", "error": None})
	frappe.enqueue(
		"neotecai_intake.services.extract.run",
		queue="long",
		timeout=1800,
		intake_name=intake_name,
		enqueue_after_commit=True,
		job_id=f"neotecai_intake::{intake_name}",
		deduplicate=True,
	)


def run(intake_name):
	"""Background job entry point."""
	if not frappe.db.exists("NeotecAI Document Intake", intake_name):
		return
	doc = frappe.get_doc("NeotecAI Document Intake", intake_name)
	if doc.purchase_invoice or doc.expense_claim or doc.status in ("Created", "Completed", "Cancelled"):
		return  # never overwrite an intake that already produced a document
	started = time.monotonic()
	doc.db_set("status", "Processing", notify=True)
	frappe.db.commit()
	try:
		extract_into(doc)
		doc.processing_seconds = round(time.monotonic() - started, 1)
		doc.flags.from_extraction = True
		doc.save(ignore_permissions=True)
	except Exception as e:
		frappe.db.rollback()
		doc.reload()
		doc.db_set({
			"status": "Failed",
			"error": str(e)[:1000],
			"processing_seconds": round(time.monotonic() - started, 1),
		}, notify=True)
		frappe.log_error(f"NeotecAI Intake extraction failed: {intake_name}")
	frappe.db.commit()
	frappe.publish_realtime("neotecai_intake_done", {"name": intake_name}, doctype="NeotecAI Document Intake",
		docname=intake_name, after_commit=True)


def extract_into(doc):
	settings = llm.get_settings()
	content, file_name = files.load_file(doc.source_file)
	doc.file_hash = files.file_hash(content)
	prepared = files.prepare(content, file_name, settings.max_pages or 3, settings.image_max_side or 1600)

	cats = [c.category for c in settings.categories] or ["Other"]
	hints = [c.hint or "" for c in settings.categories] or [""]
	data, raw = llm.chat_json(build_messages(prepared, cats, hints), build_schema(cats), settings)
	doc.raw_response = json.dumps(data, ensure_ascii=False, indent=1)

	qr = files.read_zatca_qr(prepared["full_images"])
	apply(doc, data, qr, settings)


# --------------------------------------------------------------------------- mapping

def _num(v):
	if isinstance(v, (int, float)):
		return float(v)
	s = digits_only_keep_dot(v)
	try:
		return float(s) if s else 0.0
	except ValueError:
		return 0.0


def digits_only_keep_dot(v):
	from neotecai_intake.services.matching import ARABIC_DIGITS

	s = str(v or "").translate(ARABIC_DIGITS).replace("٫", ".").replace(",", "")
	return "".join(ch for ch in s if ch.isdigit() or ch in ".-")


def apply(doc, data, qr, settings):
	tol = flt(settings.amount_tolerance) or 0.05
	std_rate = flt(settings.default_vat_rate) or 15
	warnings = []

	doc.document_kind = KIND_MAP.get(data.get("document_kind"), "Other")
	doc.supplier_name_extracted = (data.get("supplier_name") or "").strip()[:140]
	doc.supplier_vat_extracted = digits_only(data.get("supplier_vat_number"))[:20]
	doc.bill_no = (data.get("invoice_number") or "").strip()[:140]

	try:
		doc.bill_date = getdate(data.get("invoice_date")) if data.get("invoice_date") else None
	except Exception:
		doc.bill_date = None
		warnings.append(_("Invoice date could not be read: {0}").format(data.get("invoice_date")))

	currency = (data.get("currency") or "").strip().upper()
	currency = {"SR": "SAR", "ر.س": "SAR", "ريال": "SAR", "RIYAL": "SAR"}.get(currency, currency)
	doc.currency = currency if currency and frappe.db.exists("Currency", currency) else (
		frappe.get_cached_value("Company", doc.company, "default_currency"))

	doc.net_total = _num(data.get("net_total"))
	doc.vat_total = _num(data.get("vat_total"))
	doc.grand_total = _num(data.get("grand_total"))

	# ---- ZATCA QR is authoritative for seller VAT no., totals and date
	doc.qr_verified = 0
	doc.qr_data = None
	if qr:
		doc.qr_data = json.dumps(qr, ensure_ascii=False)
		if qr.get("vat_number"):
			if doc.supplier_vat_extracted and doc.supplier_vat_extracted != qr["vat_number"]:
				warnings.append(_("VAT number read from text ({0}) differs from QR ({1}); QR used").format(
					doc.supplier_vat_extracted, qr["vat_number"]))
			doc.supplier_vat_extracted = qr["vat_number"]
		if not doc.supplier_name_extracted and qr.get("seller_name"):
			doc.supplier_name_extracted = qr["seller_name"]
		ok = True
		if qr.get("total") is not None and abs(qr["total"] - doc.grand_total) > tol:
			warnings.append(_("Grand total {0} differs from QR total {1}; QR used").format(
				doc.grand_total, qr["total"]))
			doc.grand_total = qr["total"]
			ok = False
		if qr.get("vat_total") is not None and abs(qr["vat_total"] - doc.vat_total) > tol:
			warnings.append(_("VAT {0} differs from QR VAT {1}; QR used").format(doc.vat_total, qr["vat_total"]))
			doc.vat_total = qr["vat_total"]
			ok = False
		if qr.get("date") and not doc.bill_date:
			doc.bill_date = qr["date"]
		doc.qr_verified = 1 if ok else 0

	if not doc.net_total and doc.grand_total:
		doc.net_total = flt(doc.grand_total - doc.vat_total, 2)
	if not doc.grand_total and doc.net_total:
		doc.grand_total = flt(doc.net_total + doc.vat_total, 2)

	# ---- lines
	cmap = category_map(settings)
	doc.set("items", [])
	for line in data.get("lines") or []:
		amount = _num(line.get("amount_excl_vat"))
		qty = _num(line.get("quantity")) or 1
		rate = _num(line.get("unit_price_excl_vat")) or (amount / qty if qty else amount)
		if not amount:
			amount = flt(rate * qty, 2)
		if not amount and not line.get("description"):
			continue
		cat = (line.get("category") or "").strip()
		mapping = cmap.get(cat.lower())
		doc.append("items", {
			"description": (line.get("description") or cat or _("Item"))[:1000],
			"category": cat,
			"item_code": (mapping.item_code if mapping else None) or settings.default_item,
			"expense_claim_type": mapping.expense_claim_type if mapping else None,
			"qty": qty,
			"rate": rate,
			"amount": amount,
			"vat_rate": _num(line.get("vat_rate")),
			"vat_amount": _num(line.get("vat_amount")),
		})

	if not doc.items and doc.grand_total:
		fallback = settings.categories[0] if settings.categories else None
		doc.append("items", {
			"description": doc.supplier_name_extracted or _("Invoice total"),
			"category": fallback.category if fallback else "",
			"item_code": (fallback.item_code if fallback else None) or settings.default_item,
			"expense_claim_type": fallback.expense_claim_type if fallback else None,
			"qty": 1, "rate": doc.net_total, "amount": doc.net_total,
			"vat_rate": std_rate if doc.vat_total else 0, "vat_amount": doc.vat_total,
		})
		warnings.append(_("No line items found; one line was created from the totals"))

	# ---- consistency checks
	lines_net = sum(flt(i.amount) for i in doc.items)
	if doc.items and abs(lines_net - doc.net_total) > max(tol, 0.01 * len(doc.items)):
		warnings.append(_("Lines add up to {0} but net total is {1}").format(round(lines_net, 2), doc.net_total))
	if abs(doc.net_total + doc.vat_total - doc.grand_total) > tol:
		warnings.append(_("Net {0} + VAT {1} does not equal total {2}").format(
			doc.net_total, doc.vat_total, doc.grand_total))
	if doc.vat_total and doc.net_total:
		eff = doc.vat_total / doc.net_total * 100
		if abs(eff - std_rate) > 0.5:
			warnings.append(_("VAT is {0}% of net, expected {1}%").format(round(eff, 2), std_rate))

	vat_ok = is_valid_ksa_vat(doc.supplier_vat_extracted)
	if doc.supplier_vat_extracted and not vat_ok:
		warnings.append(_("Supplier VAT number {0} is not a valid 15-digit Saudi VAT number").format(
			doc.supplier_vat_extracted))
	if doc.vat_total and not doc.supplier_vat_extracted:
		warnings.append(_("VAT charged but no supplier VAT number found; input VAT not recoverable"))
	doc.vat_recoverable = 1 if (vat_ok and doc.vat_total > 0 and doc.document_kind != "Other") else 0

	if doc.bill_date:
		bd = getdate(doc.bill_date)
		if bd > getdate(add_days(nowdate(), 1)):
			warnings.append(_("Invoice date {0} is in the future").format(doc.bill_date))
		elif bd < getdate(add_days(nowdate(), -365)):
			warnings.append(_("Invoice date {0} is more than a year old").format(doc.bill_date))
	else:
		warnings.append(_("Invoice date missing"))

	if not doc.bill_no:
		warnings.append(_("Invoice number missing"))

	# ---- supplier
	if not doc.supplier:
		supplier, how = find_supplier(doc.supplier_name_extracted, doc.supplier_vat_extracted)
		doc.supplier = supplier
		if supplier and how != "VAT number":
			warnings.append(_("Supplier matched by {0}: please confirm").format(how))
	if not doc.supplier and doc.create_as == "Purchase Invoice":
		if settings.auto_create_supplier:
			warnings.append(_("Supplier not found; it will be created on approval"))
		else:
			warnings.append(_("Supplier not found; select or create it"))

	if data.get("payment_status") == "paid" and doc.create_as == "Purchase Invoice" \
			and doc.payment_handling == "Unpaid - pay later":
		warnings.append(_("Document looks already paid ({0}); consider 'Already paid'").format(
			data.get("payment_method") or _("receipt")))

	warnings.extend(duplicate_warnings(doc))

	doc.warnings = "\n".join(f"• {w}" for w in warnings) or None
	doc.confidence = max(0, 100 - 12 * len(warnings)) if not doc.qr_verified else max(0, 100 - 8 * len(warnings))
	doc.status = "Needs Review" if warnings else "Ready"


def duplicate_warnings(doc):
	out = []
	if doc.supplier and doc.bill_no:
		pi = frappe.db.get_value("Purchase Invoice",
			{"supplier": doc.supplier, "bill_no": doc.bill_no, "docstatus": ["<", 2]}, "name")
		if pi:
			out.append(_("Possible duplicate: Purchase Invoice {0} has the same supplier and invoice no.").format(pi))
	if doc.file_hash:
		other = frappe.db.get_value("NeotecAI Document Intake",
			{"file_hash": doc.file_hash, "name": ["!=", doc.name], "status": ["!=", "Cancelled"]}, "name")
		if other:
			out.append(_("The same file was already uploaded in {0}").format(other))
	return out
