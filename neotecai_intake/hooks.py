app_name = "neotecai_intake"
app_title = "NeotecAI Intake"
app_publisher = "Neotec"
app_description = "AI document intake (supplier invoices, receipts) for ERPNext using LocalAI"
app_email = "admin@example.com"
app_license = "mit"
required_apps = ["erpnext"]

after_install = "neotecai_intake.install.after_install"

doc_events = {
    "Purchase Invoice": {
        "on_submit": "neotecai_intake.events.on_purchase_invoice_submit",
        "on_cancel": "neotecai_intake.events.on_target_cancel",
        "on_trash": "neotecai_intake.events.on_target_trash",
    },
    "Expense Claim": {
        "on_submit": "neotecai_intake.events.on_expense_claim_submit",
        "on_cancel": "neotecai_intake.events.on_target_cancel",
        "on_trash": "neotecai_intake.events.on_target_trash",
    },
    "File": {
        "after_insert": "neotecai_intake.events.on_file_after_insert",
    },
}
