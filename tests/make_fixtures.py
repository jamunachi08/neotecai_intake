import base64, sys
from PIL import Image, ImageDraw, ImageFont
import qrcode

def tlv(seller, vat, ts, total, vat_total):
    out = b""
    for tag, val in enumerate([seller, vat, ts, total, vat_total], start=1):
        b = val.encode()
        out += bytes([tag, len(b)]) + b
    return base64.b64encode(out).decode()

def font(size):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        try: return ImageFont.truetype(p, size)
        except Exception: pass
    return ImageFont.load_default()

def page(lines, qr_text=None, size=(1240, 1754)):
    img = Image.new("RGB", size, "white"); d = ImageDraw.Draw(img); y = 80
    for txt, s in lines:
        d.text((80, y), txt, fill="black", font=font(s)); y += s + 22
    if qr_text:
        q = qrcode.make(qr_text, box_size=6, border=2).convert("RGB")
        img.paste(q, (size[0] - q.size[0] - 80, 80))
    return img

out = sys.argv[1]
# 1. Tax invoice PDF with ZATCA QR
qr1 = tlv("Al Noor Office Supplies", "300000000000003", "2026-10-20T10:15:00Z", "1150.00", "150.00")
page([("TAX INVOICE  فاتورة ضريبية", 44), ("Al Noor Office Supplies", 32), ("VAT No: 300000000000003", 28),
      ("Invoice No: INV-2026-0042   Date: 2026-10-20", 28), ("Bill to: Test KSA Co", 28), ("", 20),
      ("Printer paper A4 (box)   10 x 50.00 = 500.00", 28), ("Toner cartridge HP 26A   2 x 250.00 = 500.00", 28),
      ("Subtotal 1,000.00   VAT 15% 150.00   Total 1,150.00 SAR", 30)], qr1).save(f"{out}/tax_invoice.pdf")
# 2. Restaurant receipt JPG (simplified tax invoice) for an expense claim
qr2 = tlv("Najd Grill Restaurant", "310122393500003", "2026-10-25T20:40:00Z", "115.00", "15.00")
page([("فاتورة ضريبية مبسطة", 36), ("Najd Grill Restaurant", 30), ("VAT 310122393500003", 26), ("Receipt 88231  2026-10-25", 26),
      ("Mixed grill x2  100.00", 26), ("VAT 15%  15.00", 26), ("TOTAL 115.00  PAID MADA", 30)], qr2, size=(700, 1100)).save(f"{out}/restaurant_receipt.jpg", quality=90)
# 3. Fuel receipt, no VAT number (VAT not recoverable), already paid
page([("PETROL STATION 24", 34), ("Receipt 5512   2026-10-26", 26), ("Diesel 50 L   92.00", 26), ("incl. VAT 12.00", 26), ("TOTAL 92.00 CASH", 30)],
     size=(700, 900)).save(f"{out}/fuel_receipt.png")
print("fixtures ok")
