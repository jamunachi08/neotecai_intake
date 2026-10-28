import frappe

DEFAULT_CATEGORIES = [
	("Office Supplies", "stationery, printer ink, small office items"),
	("Fuel and Vehicle", "petrol, diesel, car wash, vehicle repairs, parking, tolls"),
	("Travel and Accommodation", "flights, hotels, taxis, Uber/Careem, visas"),
	("Meals and Entertainment", "restaurants, coffee, catering, client hospitality"),
	("Utilities and Telecom", "electricity, water, internet, mobile (STC, Mobily, Zain)"),
	("IT and Software", "software subscriptions, cloud, computers, accessories"),
	("Repairs and Maintenance", "building or equipment repairs, spare parts, cleaning"),
	("Professional Services", "consulting, legal, audit, government fees, translation"),
	("Rent", "office, warehouse or equipment rent"),
	("Goods and Materials", "goods for resale, raw materials, inventory purchases"),
	("Other", "anything else"),
]


def after_install():
	settings = frappe.get_single("NeotecAI Intake Settings")
	if not settings.categories:
		for cat, hint in DEFAULT_CATEGORIES:
			settings.append("categories", {"category": cat, "hint": hint})
	settings.flags.ignore_mandatory = True
	settings.save(ignore_permissions=True)
