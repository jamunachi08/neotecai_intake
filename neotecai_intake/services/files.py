"""Turn an attached PDF/image into model-ready images, text layer and ZATCA QR data."""

import base64
import hashlib
import io
import os
from datetime import datetime

import frappe
from frappe import _
from PIL import Image, ImageOps

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
SUPPORTED_EXT = IMAGE_EXT | {".pdf"}

try:  # optional: iPhone photos
	from pillow_heif import register_heif_opener

	register_heif_opener()
except Exception:
	pass


def is_supported(file_name):
	return os.path.splitext((file_name or "").lower())[1] in SUPPORTED_EXT


def load_file(file_url):
	"""Return (bytes, file_name) for a File attached by URL."""
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		frappe.throw(_("File {0} not found").format(file_url))
	file_doc = frappe.get_doc("File", name)
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode()
	return content, file_doc.file_name or file_url


def file_hash(content):
	return hashlib.sha256(content).hexdigest()


def prepare(content, file_name, max_pages=3, max_side=1600):
	"""Return dict(images=[PIL], text=str, pages=int)."""
	ext = os.path.splitext(file_name.lower())[1]
	if ext == ".pdf":
		return _from_pdf(content, max_pages, max_side)
	if ext in IMAGE_EXT:
		img = Image.open(io.BytesIO(content))
		img = ImageOps.exif_transpose(img).convert("RGB")
		return {"images": [_fit(img, max_side)], "full_images": [img], "text": "", "pages": 1}
	frappe.throw(_("Unsupported file type {0}. Use PDF, JPG, PNG or WEBP.").format(ext or file_name))


def _from_pdf(content, max_pages, max_side):
	import pypdfium2 as pdfium

	pdf = pdfium.PdfDocument(content)
	images, full, texts = [], [], []
	try:
		total = len(pdf)
		for i in range(min(total, max_pages or 3)):
			page = pdf[i]
			try:
				tp = page.get_textpage()
				texts.append(tp.get_text_range() or "")
				tp.close()
			except Exception:
				pass
			w, h = page.get_size()
			# render at ~200 dpi but never wider than needed for QR decoding
			scale = min(200 / 72, 2600 / max(w, h))
			bitmap = page.render(scale=scale)
			img = bitmap.to_pil().convert("RGB")
			full.append(img)
			images.append(_fit(img, max_side))
			page.close()
	finally:
		pdf.close()
	return {"images": images, "full_images": full, "text": "\n".join(texts).strip(), "pages": total}


def _fit(img, max_side):
	img = img.copy()
	img.thumbnail((max_side, max_side))
	return img


def to_data_url(img):
	buf = io.BytesIO()
	img.save(buf, format="JPEG", quality=85)
	return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ ZATCA QR

def read_zatca_qr(images):
	"""Decode a Saudi e-invoice QR (TLV, base64) from any of the page images."""
	try:
		import zxingcpp
	except Exception:
		return None
	for img in images:
		try:
			results = zxingcpp.read_barcodes(img)
		except Exception:
			continue
		for res in results:
			data = parse_zatca_tlv(res.text)
			if data:
				return data
	return None


def parse_zatca_tlv(text):
	"""ZATCA TLV: tag 1 seller, 2 VAT no., 3 timestamp, 4 total incl. VAT, 5 VAT total."""
	try:
		raw = base64.b64decode((text or "").strip(), validate=True)
	except Exception:
		return None
	out, i = {}, 0
	try:
		while i + 2 <= len(raw):
			tag, length = raw[i], raw[i + 1]
			value = raw[i + 2 : i + 2 + length]
			i += 2 + length
			if tag in (1, 2, 3, 4, 5):
				out[tag] = value.decode("utf-8")
	except Exception:
		return None
	if not {1, 2, 4}.issubset(out):
		return None
	result = {
		"seller_name": out.get(1),
		"vat_number": out.get(2),
		"timestamp": out.get(3),
		"total": _num(out.get(4)),
		"vat_total": _num(out.get(5)),
	}
	result["date"] = _date(out.get(3))
	return result


def _num(v):
	try:
		return float(v)
	except (TypeError, ValueError):
		return None


def _date(ts):
	if not ts:
		return None
	try:
		return datetime.fromisoformat(ts.strip().replace("Z", "+00:00")).date().isoformat()
	except ValueError:
		pass
	for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
		try:
			return datetime.strptime(ts.strip()[:19], fmt).date().isoformat()
		except ValueError:
			continue
	return None
