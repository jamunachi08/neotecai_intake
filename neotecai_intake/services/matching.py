"""Match extracted text to ERPNext master data."""

import difflib
import re

import frappe

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def digits_only(value):
	return re.sub(r"\D", "", str(value or "").translate(ARABIC_DIGITS))


def is_valid_ksa_vat(vat):
	"""Saudi VAT registration numbers are 15 digits, starting and ending with 3."""
	vat = digits_only(vat)
	return len(vat) == 15 and vat.startswith("3") and vat.endswith("3")


def _norm(name):
	name = (name or "").lower()
	name = re.sub(r"\b(co|company|est|establishment|llc|ltd|limited|trading|for|and|the)\b", " ", name)
	name = re.sub(r"(شركة|مؤسسة|للتجارة|المحدودة|ذ\.م\.م)", " ", name)
	return re.sub(r"[\W_]+", " ", name).strip()


def find_supplier(name, vat):
	"""Return (supplier, how) or (None, None)."""
	vat = digits_only(vat)
	if vat:
		hit = frappe.db.get_value("Supplier", {"tax_id": vat, "disabled": 0}, "name")
		if hit:
			return hit, "VAT number"
		# tax_id stored with spaces/dashes
		for s in frappe.get_all("Supplier", filters={"disabled": 0, "tax_id": ["is", "set"]},
				fields=["name", "tax_id"]):
			if digits_only(s.tax_id) == vat:
				return s.name, "VAT number"
	if not name:
		return None, None
	hit = frappe.db.get_value("Supplier", {"supplier_name": name, "disabled": 0}, "name")
	if hit:
		return hit, "exact name"
	target = _norm(name)
	if not target:
		return None, None
	best, best_score = None, 0.0
	for s in frappe.get_all("Supplier", filters={"disabled": 0}, fields=["name", "supplier_name"]):
		score = difflib.SequenceMatcher(None, target, _norm(s.supplier_name)).ratio()
		if score > best_score:
			best, best_score = s.name, score
	if best_score >= 0.88:
		return best, f"similar name ({int(best_score * 100)}%)"
	return None, None


def category_map(settings):
	return {(c.category or "").strip().lower(): c for c in settings.get("categories") or []}


def company_accounts(settings, company):
	for row in settings.get("company_accounts") or []:
		if row.company == company:
			return row
	return None
