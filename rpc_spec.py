# -*- coding: utf-8 -*-
"""Which data-layer methods the browser may call, and the permission each needs.

name -> (permission | tuple(any of) | None for every logged-in user, target)
target "db"   = the company open in this browser
target "main" = the main database (users, companies, WhatsApp inbox, internet leads)
Write methods are also checked inside the data layer (users.GUARDED).
"""
V, E = "clients_view", "clients_edit"
EV, EE = "employees_view", "employees_edit"
TV, TE = "tx_view", "tx_edit"
SV, SE = "sales_view", "sales_edit"
ACC = "accounting"

RPC = {
    # ---------- lists & look-ups ----------
    "client_choices": (None, "db"),
    "reps": (None, "db"),
    "company_name": (None, "db"),
    "currency": (None, "db"),
    "final_stage": (None, "db"),
    # ---------- clients ----------
    "clients": (V, "db"), "client": (V, "db"), "save_client": (E, "db"), "delete_client": ("clients_delete", "db"),
    "find_duplicate_client": (V, "db"), "client_financial_count": (V, "db"),
    "owners": (V, "db"), "owner": (V, "db"), "save_owner": (E, "db"), "delete_owner": (E, "db"),
    "documents": (V, "db"), "document": (V, "db"), "save_document": (E, "db"), "delete_document": (E, "db"),
    "expiring_items": (None, "db"),
    "files": (None, "db"), "file": (None, "db"), "delete_file": ("files", "db"),
    "update_file_category": ("files", "db"),
    # ---------- employees ----------
    "employees": (EV, "db"), "employee": (EV, "db"), "save_employee": (EE, "db"),
    "delete_employee": (EE, "db"), "find_duplicate_employee": (EV, "db"),
    # ---------- transactions & payments ----------
    "transactions": (TV, "db"), "transaction": (TV, "db"), "save_transaction": (TE, "db"),
    "delete_transaction": ("tx_delete", "db"), "action_needed": (TV, "db"), "tx_due": ("money", "db"),
    "paid_total": ("money", "db"), "log": (TV, "db"), "add_log": (TE, "db"),
    "sys_account": (None, "db"), "account_label": (None, "db"),
    "add_payment": ("receipts", "db"), "update_payment": ("receipts", "db"), "delete_payment": ("sales_delete", "db"),
    "payment": ((SV, "receipts"), "db"), "payments": ((SV, "receipts", "money"), "db"),
    # ---------- quotations & invoices ----------
    "quotations": (SV, "db"), "quotation": (SV, "db"), "save_quotation": (SE, "db"),
    "delete_quotation": ("sales_delete", "db"),
    "invoices": (SV, "db"), "invoice": (SV, "db"), "invoice_paid": (SV, "db"), "save_invoice": (SE, "db"),
    "delete_invoice": ("sales_delete", "db"),
    # ---------- expenses & finance ----------
    "expenses": ("expenses", "db"), "expense": ("expenses", "db"), "save_expense": ("expenses", "db"),
    "delete_expense": ("expenses", "db"),
    "finance_summary": (("finance", "money"), "db"), "outstanding_by_client": (("finance", "money"), "db"),
    "monthly_report": ("finance", "db"), "expenses_by_category": (("finance", "expenses"), "db"),
    "client_statement": (("finance", SV), "db"),
    # ---------- tasks ----------
    "tasks": ("tasks", "db"), "task": ("tasks", "db"), "save_task": ("tasks", "db"),
    "set_task_done": ("tasks", "db"), "delete_task": ("tasks", "db"),
    # ---------- templates ----------
    "templates": (None, "db"), "template": (None, "db"), "save_template": ("templates", "db"),
    "delete_template": ("templates", "db"),
    # ---------- accounting ----------
    "accounts": (ACC, "db"), "account": (ACC, "db"), "save_account": ("accounting_edit", "db"),
    "delete_account": ("accounting_edit", "db"), "account_in_use": (ACC, "db"), "next_child_code": (ACC, "db"),
    "account_tree": (ACC, "db"), "account_balance": (ACC, "db"), "ledger": (ACC, "db"),
    "trial_balance": (ACC, "db"), "income_statement": (ACC, "db"), "balance_sheet": (ACC, "db"),
    "ar_balances": ((ACC, "finance"), "db"), "client_ledger": ((ACC, "finance", SV), "db"),
    "entries": (ACC, "db"), "entry": (ACC, "db"), "entry_lines": (ACC, "db"),
    "save_manual_entry": ("accounting_edit", "db"), "delete_entry": ("accounting_edit", "db"),
    "vouchers": ((ACC, "expenses"), "db"), "voucher": ((ACC, "expenses"), "db"),
    "save_voucher": ((ACC, "expenses"), "db"), "delete_voucher": ("accounting_edit", "db"),
    "lock_date": (None, "db"), "money_account_for_method": (None, "db"),
    "rebuild_all_entries": ("accounting_edit", "db"),
    # ---------- renewals ----------
    "renewal_items": ("renewals", "db"), "set_followup": ("renewals", "db"), "due_reminders": ("renewals", "db"),
    "reminder_text": ("renewals", "db"), "reminder_log": ("renewals", "db"), "log_reminder": ("renewals", "db"),
    "rem_days": ("renewals", "db"), "rem_types": ("renewals", "db"), "wa_ready": (None, "main"),
    "due_stage": ("renewals", "db"), "item_line": ("renewals", "db"), "api_params": ("renewals", "db"),
    "rem_weekly_after": ("renewals", "db"),
    # ---------- CRM ----------
    "crm_stages": ("crm", "db"), "leads": ("crm", "db"), "lead": ("crm", "db"), "save_lead": ("crm", "db"),
    "set_lead_stage": ("crm", "db"), "delete_lead": ("crm_delete", "db"), "follow_ups_due": ("crm", "db"),
    "find_lead_duplicates": ("crm", "db"), "add_activity": ("crm", "db"), "activities": ("crm", "db"),
    "convert_lead": ("crm", "db"), "receive_lead": ("crm", "db"), "segment_recipients": ("crm_campaigns", "db"),
    "save_campaign": ("crm_campaigns", "db"), "campaigns": ("crm_campaigns", "db"),
    "campaign": ("crm_campaigns", "db"), "crm_report": ("crm_reports", "db"),
    # ---------- users & activity log (main database) ----------
    "users": ("users", "main"), "user_row": ("users", "main"), "create_user": ("users", "main"),
    "update_user": ("users", "main"), "set_password": (None, "main"), "delete_user": ("users", "main"),
    "audit_rows": ("users", "main"), "company_users": ("users", "main"),
    "set_company_users": (("users", "companies"), "main"),
    # ---------- companies ----------
    "companies": (None, "main"), "company_row": (None, "main"), "create_company": ("companies", "main"),
    "set_company_active": ("companies", "main"),
    # ---------- client companies (business) ----------
    "vat_rate": (None, "db"),
    "items": (("inventory", SV, "purchases"), "db"), "item": (("inventory", SV, "purchases"), "db"),
    "save_item": ("inventory", "db"), "delete_item": ("inventory", "db"), "stock_on_hand": (("inventory", SV), "db"),
    "inventory_valuation": ("inventory", "db"), "item_card": ("inventory", "db"), "low_stock": (None, "db"),
    "suppliers": ("purchases", "db"), "supplier": ("purchases", "db"), "save_supplier": ("purchases", "db"),
    "delete_supplier": ("purchases", "db"), "ap_balances": ("purchases", "db"), "supplier_ledger": ("purchases", "db"),
    "purchase_bills": ("purchases", "db"), "purchase_bill": ("purchases", "db"), "bill_totals": ("purchases", "db"),
    "bill_paid": ("purchases", "db"), "save_purchase_bill": ("purchases", "db"),
    "delete_purchase_bill": ("purchases", "db"),
    "supplier_payments": ("purchases", "db"), "supplier_payment": ("purchases", "db"),
    "save_supplier_payment": ("purchases", "db"), "delete_supplier_payment": ("purchases", "db"),
    "sales_invoices": (SV, "db"), "sales_invoice": (SV, "db"), "invoice_totals": (SV, "db"),
    "invoice_received": (SV, "db"), "save_sales_invoice": (SE, "db"), "delete_sales_invoice": ("sales_delete", "db"),
    "sales_receipts": ((SV, "receipts"), "db"), "sales_receipt": ((SV, "receipts"), "db"),
    "save_sales_receipt": ("receipts", "db"), "delete_sales_receipt": ("sales_delete", "db"),
    "stock_adjustments": ("inventory", "db"), "save_stock_adjustment": ("inventory", "db"),
    "delete_stock_adjustment": ("inventory", "db"),
    "vat_report": (ACC, "db"), "sales_by_item": ((ACC, SV), "db"), "period_totals": (None, "db"),
    "money_balance": (("finance", ACC), "db"), "unpaid_sales_invoices": ((SV, "receipts"), "db"),
    "unpaid_bills": ("purchases", "db"),
    "company_docs": ("company_files", "db"), "company_doc": ("company_files", "db"),
    "save_company_doc": ("company_files", "db"), "delete_company_doc": ("company_files", "db"),
    "expiring_company_docs": (None, "db"),
    # ---------- WhatsApp inbox (main company) ----------
    "inbox_chats": ("inbox", "main", "main_only"), "inbox_chat": ("inbox", "main", "main_only"),
    "inbox_messages": ("inbox", "main", "main_only"), "inbox_message": ("inbox", "main", "main_only"),
    "inbox_mark_read": ("inbox", "main", "main_only"), "inbox_unread_total": ("inbox", "main", "main_only"),
    "inbox_window_open": ("inbox", "main", "main_only"), "inbox_set_link": ("inbox", "main", "main_only"),
    "inbox_save_to_client": ("inbox", "main", "main_only"), "inbox_delete_failed": ("inbox", "main", "main_only"),
    "inbox_relink_all": ("inbox", "main", "main_only"),
}

# keys removed from results for users without the permission
SALARY_KEYS = {"salary", "bank_name", "bank_account", "iban"}
MONEY_KEYS = {"fee", "gov_fees", "due", "paid", "remaining", "discount", "invoiced", "total_due",
              "outstanding", "amount_due", "value", "won_value", "open_value"}
MONEY_METHODS = {"transactions", "transaction", "action_needed", "clients", "client", "leads", "lead",
                 "crm_report", "follow_ups_due"}

# save_xxx(data, id): fields missing from data are taken from the stored record
PRESERVE = {
    "save_transaction": ("transaction", ["fee", "gov_fees", "revenue_account_id", "gov_account_id", "zoho_invoice",
                                         "employee_id", "assigned_user_id", "entity", "notes", "start_date",
                                         "due_date", "status", "tx_type"]),
    "save_lead": ("lead", ["value", "lost_reason", "client_id", "assigned_user_id", "notes", "stage",
                           "next_follow_up"]),
    "save_employee": ("employee", ["salary", "bank_name", "bank_account", "iban", "status", "notes"]),
    "save_client": ("client", ["bank_name", "bank_account", "iban", "status", "notes"]),
}
