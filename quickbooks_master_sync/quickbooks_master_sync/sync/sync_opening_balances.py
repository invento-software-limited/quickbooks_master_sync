import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from ..utils.logging import qb_log_error, qb_log_exception, qb_log_status
from ..utils.multi_company_utils import get_buying_price_list_for_company
from ..utils.qb_id_utils import make_unique_qb_id
from .sync_utils import _dbg as _dbg_common
from .sync_utils import _get_quickbooks_company as _get_quickbooks_company_base
from .sync_utils import (
	ensure_fiscal_year_for_date,
	query_with_pagination,
	save_qb_data_to_json,
)


def _dbg(event, payload=None):
	"""Debug logging helper for opening balance sync"""
	_dbg_common("sync_opening_balances", event, payload)


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None, section="sync_opening_balances"):
	"""Wrapper for sync_opening_balances module with section-specific logging"""
	return _get_quickbooks_company_base(
		quickbooks_obj=quickbooks_obj,
		qb_company=qb_company,
		use_cache=True,
		module_name=section,
	)


@frappe.whitelist()
def sync_opening_balances(
	quickbooks_obj: any, cutoff_date: str | None = None, auto_submit: bool | int | str = True
):
	"""
	Sync opening balances from QuickBooks to ERPNext

	Args:
	    quickbooks_obj: QuickBooks API object
	    cutoff_date: Date for opening balances (defaults to today)
	    auto_submit: If True, auto-submit journal entries. If False, create as draft

	This creates:
	1. Opening AR by Customer (Journal Entries)
	2. Opening AP by Vendor (Journal Entries)
	3. Bank balances (Journal Entries)
	4. Loan balances (Journal Entries)
	5. Inventory (Stock Reconciliation)
	6. Equity balancing entry (Journal Entry)
	"""
	from .sync_utils import reset_company_cache

	# Reset cache at start of sync
	reset_company_cache()

	_dbg("sync_opening_balances:start")
	frappe.logger().info(f"[Opening Balances] Starting sync for cutoff date: {cutoff_date or nowdate()}")

	if not cutoff_date:
		cutoff_date = nowdate()

	# Get QuickBooks synced company
	qb_company = _get_quickbooks_company(quickbooks_obj)
	if not qb_company:
		frappe.throw(_("No company found. Please sync company first."))

	_dbg("sync_opening_balances:company", {"company": qb_company, "cutoff_date": cutoff_date})
	frappe.logger().info(f"[Opening Balances] Company: {qb_company}, Cutoff Date: {cutoff_date}")

	# Initialize Cache
	from .sync_utils import SyncCache

	cache = SyncCache(qb_company)

	stats = {
		"ar_customers": 0,
		"ap_vendors": 0,
		"bank_accounts": 0,
		"loan_accounts": 0,
		"inventory_items": 0,
		"errors": 0,
		"auto_submit": auto_submit,
		"failed_accounts": [],
		"failed_customers": [],
		"failed_suppliers": [],
	}

	# 1. Sync AR by Customer
	_dbg("sync_opening_balances:ar_start")
	frappe.logger().info("[Opening Balances] Starting AR (Accounts Receivable) sync")
	sync_ar_opening_balances(quickbooks_obj, qb_company, cutoff_date, stats, auto_submit, cache=cache)

	# 2. Sync AP by Vendor
	_dbg("sync_opening_balances:ap_start")
	frappe.logger().info("[Opening Balances] Starting AP (Accounts Payable) sync")
	sync_ap_opening_balances(quickbooks_obj, qb_company, cutoff_date, stats, auto_submit, cache=cache)

	# 3. Sync Bank balances
	_dbg("sync_opening_balances:bank_start")
	frappe.logger().info("[Opening Balances] Starting Bank accounts sync")
	sync_bank_opening_balances(quickbooks_obj, qb_company, cutoff_date, stats, auto_submit, cache=cache)

	# 4. Sync Loan balances
	_dbg("sync_opening_balances:loan_start")
	frappe.logger().info("[Opening Balances] Starting Loan accounts sync")
	sync_loan_opening_balances(quickbooks_obj, qb_company, cutoff_date, stats, auto_submit, cache=cache)

	# 5. Sync Inventory
	_dbg("sync_opening_balances:inventory_start")
	frappe.logger().info("[Opening Balances] Starting Inventory sync")
	sync_inventory_opening_balances(quickbooks_obj, qb_company, cutoff_date, stats, auto_submit, cache=cache)

	# 6. Create Equity balancing entry
	_dbg("sync_opening_balances:equity_start")
	frappe.logger().info("[Opening Balances] Creating equity balancing entry")
	create_equity_balancing_entry(qb_company, cutoff_date, stats)

	_dbg("sync_opening_balances:done", stats)
	frappe.logger().info(
		f"[Opening Balances] Sync completed - Bank: {stats.get('bank_accounts', 0)}, Errors: {stats.get('errors', 0)}"
	)

	# Log summary - only show errors or success summary
	if stats.get("bank_accounts", 0) > 0:
		qb_log_status(
			title=_("Opening Balance Sync Completed"),
			method="sync_opening_balances",
			status="Success",
			message=_("Successfully processed {0} bank account(s)").format(stats.get("bank_accounts", 0)),
			module="sync_opening_balances",
			request_data={"bank_accounts": stats.get("bank_accounts", 0), "errors": stats.get("errors", 0)},
		)

	if stats.get("bank_accounts", 0) == 0 and stats.get("errors", 0) == 0:
		qb_log_status(
			title=_("No Bank Accounts Processed"),
			method="sync_opening_balances",
			status="Error",
			message=_(
				"No bank accounts were processed. This might indicate accounts were not found in ERPNext or all balances were zero."
			),
			module="sync_opening_balances",
		)

	# Log summary of failed items if any
	if stats["errors"] > 0:
		failed_summary = []
		if stats["failed_accounts"]:
			failed_summary.append(_("{0} accounts").format(len(stats["failed_accounts"])))
		if stats["failed_customers"]:
			failed_summary.append(_("{0} customers").format(len(stats["failed_customers"])))
		if stats["failed_suppliers"]:
			failed_summary.append(_("{0} suppliers").format(len(stats["failed_suppliers"])))

		if failed_summary:
			summary_msg = _(
				"Opening balance sync completed with {0} error(s). "
				"Failed items: {1}. "
				"Please check the Activity Log for details and ensure all accounts, customers, and suppliers are synced first."
			).format(stats["errors"], ", ".join(failed_summary))

			qb_log_error(
				title=_("Opening Balance Sync Completed with Errors"),
				status="Error",
				method="sync_opening_balances",
				message=summary_msg,
				module="sync_opening_balances",
				request_data={
					"failed_accounts": stats["failed_accounts"][:10],  # Limit to first 10
					"failed_customers": stats["failed_customers"][:10],
					"failed_suppliers": stats["failed_suppliers"][:10],
				},
			)

	return stats


@frappe.whitelist()
def process_ar_opening_chunk(
	customer_list: list | str,
	qb_company: str | None = None,
	cutoff_date: str | None = None,
	auto_submit: bool | int | str = False,
):
	"""Background job to process a chunk of AR Opening Balances (Customers)"""
	try:
		from quickbooks_master_sync.quickbooks_master_sync.api import _create_quickbooks_client

		quickbooks_settings = frappe.get_doc("Quickbooks Settings", "Quickbooks Settings")
		quickbooks_obj = _create_quickbooks_client(quickbooks_settings)

		qb_company = _get_quickbooks_company(quickbooks_obj=quickbooks_obj)

		# Process the chunk
		stats = sync_qb_ar_opening_chunk(customer_list, quickbooks_obj, qb_company, cutoff_date, auto_submit)

		# Log summary for this chunk
		chunk_summary_msg = f"""
AR Opening Chunk Sync Summary:
- Total in Chunk: {len(customer_list)}
- Successfully Processed: {stats.get('ar_customers', 0)}
- Errors: {stats.get('errors', 0)}
"""

		qb_log_status(
			title=_("✅ AR Opening Chunk Processed"),
			status="Success",
			method="process_ar_opening_chunk",
			message=chunk_summary_msg,
			module="sync_opening_balances",
			request_data=stats,
		)

	except Exception as e:
		qb_log_exception(
			method="process_ar_opening_chunk", err=e, message=str(e), module="sync_opening_balances"
		)


def sync_qb_ar_opening_chunk(customers, quickbooks_obj, company, cutoff_date, auto_submit=False):
	stats = {"ar_customers": 0, "errors": 0, "failed_customers": []}

	# Performance optimization: Batch commits every 100 entries
	commit_interval = 100
	last_commit_count = 0

	from .sync_utils import SyncCache

	cache = SyncCache(company)

	# Initialize generic Import Tracker
	from ..utils.import_tracker import ImportTracker

	tracker = ImportTracker(
		len(customers), "qb_ar_opening_import.log", company=company, module_name="AR_OPENING"
	)

	for qb_customer in customers:
		try:
			balance = flt(qb_customer.get("Balance", 0))
			if balance <= 0:
				continue

			customer_id = qb_customer.get("Id")
			customer_name = qb_customer.get("DisplayName")

			# Log start of processing
			# Create unique ID for the tracker (use JE ID as requested)

			raw_je_id = f"AR-OPEN-{customer_id}"
			unique_je_id = make_unique_qb_id(raw_je_id, company)

			# Log start of processing
			tracker_id = f"{unique_je_id} ({customer_name})"
			tracker.log_processing("QuickBooks", tracker_id)

			# Early check: Skip if AR Opening Entry already exists
			qb_je_id = f"AR-OPEN-{customer_id}"
			# We need to construct the full unique ID including company, just like in create_ar_opening_journal_entry
			unique_je_id = make_unique_qb_id(qb_je_id, company)

			# Check if JE exists
			existing_je = frappe.db.get_value(
				"Journal Entry", {"quickbooks_journal_entry_id": unique_je_id}, "name"
			)

			if existing_je:
				stats["ar_customers"] += 1  # Count as processed/skipped
				tracker.log_skip("QuickBooks", tracker_id, "already_exists")
				continue

			# Find corresponding ERPNext customer using unique QB ID
			unique_customer_id = make_unique_qb_id(customer_id, company)

			# Optimized: Use cache
			erp_customer = None
			cust_name = cache.get_customer(unique_customer_id)
			if cust_name:
				from types import SimpleNamespace

				erp_customer = SimpleNamespace(name=cust_name)

			if not erp_customer:
				erp_customer = frappe.db.get_value(
					"Customer",
					{"quickbooks_cust_id": unique_customer_id, "custom_company": company},
					["name", "customer_name"],
					as_dict=True,
				)

			if not erp_customer:
				_(
					"Customer '{0}' (QuickBooks ID: {1}) not found in ERPNext. "
					"Please sync customers first or ensure the customer exists with quickbooks_cust_id = {1}."
				).format(customer_name, customer_id)

				_dbg(
					"sync_ar_opening_balances:customer_not_found",
					{"qb_id": customer_id, "qb_name": customer_name, "company": company},
				)

				# Only log error if not found, don't spam exception
				stats["errors"] += 1
				stats["failed_customers"].append({"name": customer_name, "qb_id": customer_id})
				tracker.log_error("QuickBooks", tracker_id, "customer_not_found_in_erpnext")
				continue

			# Create Journal Entry for AR opening balance
			create_ar_opening_journal_entry(
				company=company,
				customer=erp_customer.name,
				customer_name=customer_name,
				amount=balance,
				posting_date=cutoff_date,
				quickbooks_id=customer_id,
				auto_submit=auto_submit,
				cache=cache,
			)

			stats["ar_customers"] += 1
			tracker.log_success("QuickBooks", tracker_id, "CREATED")
			_dbg("sync_ar_opening_balances:created", {"customer": erp_customer.name, "amount": balance})

		except Exception as e:
			_dbg(
				"sync_ar_opening_balances:error",
				{"customer": qb_customer.get("DisplayName"), "error": str(e)},
			)
			stats["errors"] += 1
			qb_log_exception(
				method="sync_ar_opening_balances",
				err=e,
				request_data=qb_customer,
				module="sync_opening_balances",
			)
			tracker.log_error("QuickBooks", tracker_id, str(e))

		# Batch commit every N entries for performance
		current_processed = stats["ar_customers"] + stats["errors"]
		if current_processed > 0 and (current_processed - last_commit_count) >= commit_interval:
			frappe.db.commit()  # nosemgrep
			last_commit_count = current_processed

	# Final commit
	if stats["ar_customers"] > 0 or stats["errors"] > 0:
		frappe.db.commit()  # nosemgrep

	# Log final summary
	tracker.log_summary()
	return stats


def sync_ar_opening_balances(quickbooks_obj, company, cutoff_date, stats, auto_submit=False, cache=None):
	"""
	Sync Accounts Receivable opening balances by customer
	Creates Journal Entry with Debit to Customer and Credit to Opening Balance Equity
	"""
	try:
		# Query all customers (QuickBooks API doesn't support WHERE Balance > 0 or comparison operators)
		# We filter for Balance > 0 in Python after fetching
		customer_query = """SELECT Id, DisplayName, Balance, BalanceWithJobs FROM Customer"""
		# Use pagination helper to fetch all customers
		customers = query_with_pagination(
			quickbooks_obj,
			customer_query,
			"Customer",
			module_name="sync_opening_balances_ar",
			data_name="ar_opening_balances",
		)
		# query_with_pagination always returns a list, but ensure it's not None
		if not customers:
			customers = []

		# Save raw data for debugging
		try:
			save_qb_data_to_json(
				data=customers,
				data_name="ar_opening_balances",
				module_name="sync_opening_balances_ar",
				full_response={"QueryResponse": {"Customer": customers}},
			)
		except Exception as e:
			_dbg("sync_ar_opening_balances:debug_save_error", {"error": str(e)})

		_dbg("sync_ar_opening_balances:fetched", {"count": len(customers)})

		# Chunk the customer list into batches of 100
		chunk_size = 100
		total_customers = len(customers)

		from frappe.utils.background_jobs import enqueue

		for i in range(0, total_customers, chunk_size):
			chunk = customers[i : i + chunk_size]
			enqueue(
				method="quickbooks_master_sync.quickbooks_master_sync.sync.sync_opening_balances.process_ar_opening_chunk",
				queue="long",
				timeout=3600,
				customer_list=chunk,
				qb_company=company,
				cutoff_date=cutoff_date,
				auto_submit=auto_submit,
			)

		# Return immediately as processing is done in background
		qb_log_status(
			title=_("AR Opening Balance Sync Enqueued"),
			status="Queued",
			method="sync_ar_opening_balances",
			message=_("Enqueued {0} customers in chunks of {1} for background processing.").format(
				total_customers, chunk_size
			),
			module="sync_opening_balances",
		)

		# Update main stats to indicate queued status (optional, main caller might expect counts but here we just queue)
		# We can set a flag or just leave 0

	except Exception as e:
		_dbg("sync_ar_opening_balances:query_error", {"error": str(e)})
		qb_log_exception(
			method="sync_ar_opening_balances",
			err=e,
			request_data={},
			module="sync_opening_balances",
		)


def create_ar_opening_journal_entry(
	company, customer, customer_name, amount, posting_date, quickbooks_id, auto_submit=False, cache=None
):
	"""Create Journal Entry for AR opening balance"""

	# Check if already created
	qb_je_id = f"AR-OPEN-{quickbooks_id}"
	existing = frappe.db.get_value("Journal Entry", {"quickbooks_journal_entry_id": qb_je_id}, "name")

	if existing:
		_dbg("create_ar_opening_journal_entry:exists", {"name": existing})
		return existing

	# Get Receivable account
	company_default_receivable = None
	if cache:
		company_default_receivable = cache.get_company_default("default_receivable_account")

	if not company_default_receivable:
		company_default_receivable = frappe.db.get_value("Company", company, "default_receivable_account")
	receivable_account = None
	if company_default_receivable:
		# IMPORTANT: Validate that the account from company defaults is not a group account
		from .sync_utils import validate_account_for_transaction

		receivable_account = validate_account_for_transaction(
			company_default_receivable,
			company=company,
			account_type="Receivable",
			module_name="sync_opening_balances_ar",
		)

	if not receivable_account:
		receivable_account = frappe.db.get_value(
			"Account", {"company": company, "account_type": "Receivable", "is_group": 0}, "name"
		)

	if not receivable_account:
		frappe.throw(_("No Receivable Account found for Company {0}").format(company))

	# Ensure fiscal year exists for the posting date
	if company and posting_date:
		ensure_fiscal_year_for_date(company, posting_date, module_name="sync_opening_balances_ar")

	# Get Opening Balance Equity account
	equity_account = get_or_create_opening_balance_equity_account(company)

	# CRITICAL: Force enable customer if disabled to allow transaction creation
	# ERPNext may block transactions with disabled parties
	from .sync_utils import ensure_party_enabled

	ensure_party_enabled("Customer", customer, module_name="sync_opening_balances_ar")

	# Create Journal Entry
	from ..utils.qb_id_utils import make_unique_qb_id

	je = frappe.new_doc("Journal Entry")
	je.quickbooks_journal_entry_id = make_unique_qb_id(qb_je_id, company)
	je.voucher_type = "Opening Entry"
	je.naming_series = "JE-OB-AR-"  # AR-specific naming series
	je.posting_date = posting_date
	je.company = company
	je.multi_currency = 1  # Enable multi-currency to avoid validation errors
	je.user_remark = f"Opening AR Balance for {customer_name} (from QuickBooks)"

	# Base-currency safe amounts (ERPNext validates base debit == base credit)
	company_currency = frappe.db.get_value("Company", company, "default_currency")
	receivable_currency = (
		frappe.db.get_value("Account", receivable_account, "account_currency") or company_currency
	)
	equity_currency = frappe.db.get_value("Account", equity_account, "account_currency") or company_currency

	def _has_child_field(fieldname):
		try:
			return frappe.get_meta("Journal Entry Account").has_field(fieldname)
		except Exception:
			return False

	def _fx_rate(from_currency, to_currency, on_date):
		if not from_currency or not to_currency or from_currency == to_currency:
			return 1.0
		res = frappe.db.sql(
			"""
            SELECT exchange_rate
            FROM `tabCurrency Exchange`
            WHERE from_currency = %s
              AND to_currency = %s
              AND date <= %s
            ORDER BY date DESC
            LIMIT 1
            """,
			(from_currency, to_currency, on_date),
			as_dict=True,
		)
		if res and res[0].get("exchange_rate"):
			return float(res[0]["exchange_rate"])
		return None

	base_amount = flt(amount)

	if receivable_currency == company_currency:
		recv_amt = base_amount
		recv_ex = 1
	else:
		r = _fx_rate(receivable_currency, company_currency, posting_date) or 1
		recv_amt = base_amount / float(r)
		recv_ex = float(r)

	if equity_currency == company_currency:
		eq_amt = base_amount
		eq_ex = 1
	else:
		r = _fx_rate(equity_currency, company_currency, posting_date) or 1
		eq_amt = base_amount / float(r)
		eq_ex = float(r)

	# DYNAMIC LINKING: Automatically link Receivable account to Customer
	# Check if account is already linked to the customer
	try:
		customer_doc = frappe.get_doc("Customer", customer)
		account_exists = False
		if hasattr(customer_doc, "accounts"):
			for acc_row in customer_doc.get("accounts", []):
				if acc_row.get("account") == receivable_account and acc_row.get("company") == company:
					account_exists = True
					break

		if not account_exists:
			# Account not linked - add it dynamically
			if hasattr(customer_doc, "accounts"):
				customer_doc.append(
					"accounts",
					{
						"company": company,
						"account": receivable_account,
					},
				)
				customer_doc.save(ignore_permissions=True)
				_dbg(
					"create_ar_opening_journal_entry:customer_account_linked_dynamically",
					{
						"customer": customer,
						"account": receivable_account,
						"company": company,
						"note": f"Dynamically linked receivable account '{receivable_account}' to customer '{customer}' based on opening balance",
					},
				)
	except Exception as link_error:
		# Don't fail the journal entry if account linking fails
		_dbg(
			"create_ar_opening_journal_entry:account_linking_error",
			{
				"account": receivable_account,
				"customer": customer,
				"error": str(link_error),
				"note": "Failed to dynamically link account to customer, but continuing with journal entry",
			},
		)

	# Debit Receivable (increase customer balance)
	recv_row = {
		"account": receivable_account,
		"party_type": "Customer",
		"party": customer,
		"debit_in_account_currency": recv_amt,
		"credit_in_account_currency": 0,
		"debit": base_amount,  # Base currency amount for GL Entry
		"credit": 0,  # Base currency amount for GL Entry
	}
	if _has_child_field("account_currency"):
		recv_row["account_currency"] = receivable_currency
	if _has_child_field("exchange_rate"):
		recv_row["exchange_rate"] = recv_ex
	je.append("accounts", recv_row)

	# Credit Opening Balance Equity
	eq_row = {
		"account": equity_account,
		"debit_in_account_currency": 0,
		"credit_in_account_currency": eq_amt,
		"debit": 0,  # Base currency amount for GL Entry
		"credit": base_amount,  # Base currency amount for GL Entry
	}
	if _has_child_field("account_currency"):
		eq_row["account_currency"] = equity_currency
	if _has_child_field("exchange_rate"):
		eq_row["exchange_rate"] = eq_ex
	je.append("accounts", eq_row)

	try:
		je.flags.ignore_mandatory = True
		je.insert()

		if auto_submit:
			je.submit()
			_dbg("create_ar_opening_journal_entry:success", {"name": je.name, "status": "submitted"})
		else:
			_dbg("create_ar_opening_journal_entry:success", {"name": je.name, "status": "draft"})

		frappe.db.commit()  # nosemgrep
		return je.name
	except Exception as e:
		frappe.db.rollback()
		_dbg(
			"create_ar_opening_journal_entry:insert_error",
			{"error": str(e), "customer": customer, "amount": amount},
		)
		raise


@frappe.whitelist()
def process_ap_opening_chunk(
	vendor_list: list | str,
	qb_company: str | None = None,
	cutoff_date: str | None = None,
	auto_submit: bool | int | str = False,
):
	"""Background job to process a chunk of AP Opening Balances (Vendors)"""
	try:
		from quickbooks_master_sync.quickbooks_master_sync.api import _create_quickbooks_client

		quickbooks_settings = frappe.get_doc("Quickbooks Settings", "Quickbooks Settings")
		quickbooks_obj = _create_quickbooks_client(quickbooks_settings)

		qb_company = _get_quickbooks_company(quickbooks_obj=quickbooks_obj)

		# Process the chunk
		stats = sync_qb_ap_opening_chunk(vendor_list, quickbooks_obj, qb_company, cutoff_date, auto_submit)

		# Log summary for this chunk
		chunk_summary_msg = f"""
AP Opening Chunk Sync Summary:
- Total in Chunk: {len(vendor_list)}
- Successfully Processed: {stats.get('ap_vendors', 0)}
- Errors: {stats.get('errors', 0)}
"""

		qb_log_status(
			title=_("✅ AP Opening Chunk Processed"),
			status="Success",
			method="process_ap_opening_chunk",
			message=chunk_summary_msg,
			module="sync_opening_balances",
			request_data=stats,
		)

	except Exception as e:
		qb_log_exception(
			method="process_ap_opening_chunk", err=e, message=str(e), module="sync_opening_balances"
		)


def sync_qb_ap_opening_chunk(vendors, quickbooks_obj, company, cutoff_date, auto_submit=False):
	stats = {"ap_vendors": 0, "errors": 0, "failed_suppliers": []}

	# Performance optimization: Batch commits every 100 entries
	commit_interval = 100
	last_commit_count = 0

	from .sync_utils import SyncCache

	cache = SyncCache(company)

	# Initialize generic Import Tracker
	from ..utils.import_tracker import ImportTracker

	tracker = ImportTracker(
		len(vendors), "qb_ap_opening_import.log", company=company, module_name="AP_OPENING"
	)

	for qb_vendor in vendors:
		try:
			balance = flt(qb_vendor.get("Balance", 0))
			# QuickBooks stores vendor balances as negative when we owe them
			# We need to use absolute value and skip if zero
			if balance == 0:
				continue

			# Convert negative balance to positive (we owe the vendor)
			balance = abs(balance)

			vendor_id = qb_vendor.get("Id")
			vendor_name = qb_vendor.get("DisplayName")

			# Log start of processing
			# Create unique ID for the tracker (use JE ID as requested)

			raw_je_id = f"AP-OPEN-{vendor_id}"
			unique_je_id = make_unique_qb_id(raw_je_id, company)

			# Log start of processing
			tracker_id = f"{unique_je_id} ({vendor_name})"
			tracker.log_processing("QuickBooks", tracker_id)

			# Early check: Skip if AP Opening Entry already exists
			qb_je_id = f"AP-OPEN-{vendor_id}"
			unique_je_id = make_unique_qb_id(qb_je_id, company)

			existing_je = frappe.db.get_value(
				"Journal Entry", {"quickbooks_journal_entry_id": unique_je_id}, "name"
			)

			if existing_je:
				stats["ap_vendors"] += 1
				tracker.log_skip("QuickBooks", tracker_id, "already_exists")
				continue

			# Find corresponding ERPNext supplier using unique QB ID
			unique_vendor_id = make_unique_qb_id(vendor_id, company)

			# Optimized: Use cache
			erp_supplier = None
			supp_name = cache.get_supplier(unique_vendor_id)
			if supp_name:
				from types import SimpleNamespace

				erp_supplier = SimpleNamespace(name=supp_name)

			if not erp_supplier:
				erp_supplier = frappe.db.get_value(
					"Supplier",
					{"quickbooks_supp_id": unique_vendor_id, "custom_company": company},
					["name", "supplier_name"],
					as_dict=True,
				)

			if not erp_supplier:
				_(
					"Supplier '{0}' (QuickBooks ID: {1}) not found in ERPNext. "
					"Please sync suppliers first or ensure the supplier exists with quickbooks_supp_id = {1}."
				).format(vendor_name, vendor_id)

				_dbg(
					"sync_ap_opening_balances:vendor_not_found",
					{"qb_id": vendor_id, "qb_name": vendor_name, "company": company},
				)

				# Only log error if not found
				stats["errors"] += 1
				stats["failed_suppliers"].append({"name": vendor_name, "qb_id": vendor_id})
				tracker.log_error("QuickBooks", tracker_id, "supplier_not_found_in_erpnext")
				continue

			# Create Journal Entry for AP opening balance
			create_ap_opening_journal_entry(
				company=company,
				supplier=erp_supplier.name,
				supplier_name=vendor_name,
				amount=balance,
				posting_date=cutoff_date,
				quickbooks_id=vendor_id,
				auto_submit=auto_submit,
				cache=cache,
			)

			stats["ap_vendors"] += 1
			tracker.log_success("QuickBooks", tracker_id, "CREATED")
			_dbg("sync_ap_opening_balances:created", {"supplier": erp_supplier.name, "amount": balance})

		except Exception as e:
			_dbg("sync_ap_opening_balances:error", {"vendor": qb_vendor.get("DisplayName"), "error": str(e)})
			stats["errors"] += 1
			qb_log_exception(
				method="sync_ap_opening_balances",
				err=e,
				request_data=qb_vendor,
				module="sync_opening_balances",
			)
			tracker.log_error("QuickBooks", tracker_id, str(e))

		# Batch commit every N entries for performance
		current_processed = stats["ap_vendors"] + stats["errors"]
		if current_processed > 0 and (current_processed - last_commit_count) >= commit_interval:
			frappe.db.commit()  # nosemgrep
			last_commit_count = current_processed

	# Final commit
	if stats["ap_vendors"] > 0 or stats["errors"] > 0:
		frappe.db.commit()  # nosemgrep

	# Log final summary
	tracker.log_summary()
	return stats


def sync_ap_opening_balances(quickbooks_obj, company, cutoff_date, stats, auto_submit=False, cache=None):
	"""
	Sync Accounts Payable opening balances by vendor
	Creates Journal Entry with Credit to Vendor and Debit to Opening Balance Equity
	"""
	try:
		# Query all vendors (QuickBooks API doesn't support WHERE Balance > 0 or comparison operators)
		# We filter for Balance != 0 in Python after fetching
		vendor_query = """SELECT Id, DisplayName, Balance FROM Vendor"""
		# Use pagination helper to fetch all vendors
		vendors = query_with_pagination(
			quickbooks_obj,
			vendor_query,
			"Vendor",
			module_name="sync_opening_balances_ap",
			data_name="ap_opening_balances",
		)
		# query_with_pagination always returns a list, but ensure it's not None
		if not vendors:
			vendors = []

		# Save raw data for debugging
		try:
			save_qb_data_to_json(
				data=vendors,
				data_name="ap_opening_balances",
				module_name="sync_opening_balances_ap",
				full_response={"QueryResponse": {"Vendor": vendors}},
			)
		except Exception as e:
			_dbg("sync_ap_opening_balances:debug_save_error", {"error": str(e)})

		_dbg("sync_ap_opening_balances:fetched", {"count": len(vendors)})

		# Chunk the vendor list into batches of 100
		chunk_size = 100
		total_vendors = len(vendors)

		from frappe.utils.background_jobs import enqueue

		for i in range(0, total_vendors, chunk_size):
			chunk = vendors[i : i + chunk_size]
			enqueue(
				method="quickbooks_master_sync.quickbooks_master_sync.sync.sync_opening_balances.process_ap_opening_chunk",
				queue="long",
				timeout=3600,
				vendor_list=chunk,
				qb_company=company,
				cutoff_date=cutoff_date,
				auto_submit=auto_submit,
			)

		# Return immediately as processing is done in background
		qb_log_status(
			title=_("AP Opening Balance Sync Enqueued"),
			status="Queued",
			method="sync_ap_opening_balances",
			message=_("Enqueued {0} vendors in chunks of {1} for background processing.").format(
				total_vendors, chunk_size
			),
			module="sync_opening_balances",
		)

	except Exception as e:
		_dbg("sync_ap_opening_balances:query_error", {"error": str(e)})
		qb_log_exception(
			method="sync_ap_opening_balances",
			err=e,
			request_data={},
			module="sync_opening_balances",
		)


def create_ap_opening_journal_entry(
	company, supplier, supplier_name, amount, posting_date, quickbooks_id, auto_submit=False, cache=None
):
	"""Create Journal Entry for AP opening balance"""

	# Check if already created
	qb_je_id = f"AP-OPEN-{quickbooks_id}"
	existing = frappe.db.get_value("Journal Entry", {"quickbooks_journal_entry_id": qb_je_id}, "name")

	if existing:
		_dbg("create_ap_opening_journal_entry:exists", {"name": existing})
		return existing

	# Get Payable account
	company_default_payable = None
	if cache:
		company_default_payable = cache.get_company_default("default_payable_account")

	if not company_default_payable:
		company_default_payable = frappe.db.get_value("Company", company, "default_payable_account")
	payable_account = None
	if company_default_payable:
		# IMPORTANT: Validate that the account from company defaults is not a group account
		from .sync_utils import validate_account_for_transaction

		payable_account = validate_account_for_transaction(
			company_default_payable,
			company=company,
			account_type="Payable",
			module_name="sync_opening_balances_ap",
		)

	if not payable_account:
		payable_account = frappe.db.get_value(
			"Account", {"company": company, "account_type": "Payable", "is_group": 0}, "name"
		)

	if not payable_account:
		frappe.throw(_("No Payable Account found for Company {0}").format(company))

	# Get Opening Balance Equity account
	equity_account = get_or_create_opening_balance_equity_account(company)

	# CRITICAL: Force enable supplier if disabled to allow transaction creation
	# ERPNext may block transactions with disabled parties
	from .sync_utils import ensure_party_enabled

	ensure_party_enabled("Supplier", supplier, module_name="sync_opening_balances_ap")

	# Create Journal Entry
	from ..utils.qb_id_utils import make_unique_qb_id

	je = frappe.new_doc("Journal Entry")
	je.quickbooks_journal_entry_id = make_unique_qb_id(qb_je_id, company)
	je.voucher_type = "Opening Entry"
	je.naming_series = "JE-OB-AP-"  # AP-specific naming series
	je.posting_date = posting_date
	je.company = company
	je.multi_currency = 1  # Enable multi-currency to avoid validation errors
	je.user_remark = f"Opening AP Balance for {supplier_name} (from QuickBooks)"

	# Base-currency safe amounts (ERPNext validates base debit == base credit)
	company_currency = frappe.db.get_value("Company", company, "default_currency")
	payable_currency = frappe.db.get_value("Account", payable_account, "account_currency") or company_currency
	equity_currency = frappe.db.get_value("Account", equity_account, "account_currency") or company_currency

	def _has_child_field(fieldname):
		try:
			return frappe.get_meta("Journal Entry Account").has_field(fieldname)
		except Exception:
			return False

	def _fx_rate(from_currency, to_currency, on_date):
		if not from_currency or not to_currency or from_currency == to_currency:
			return 1.0
		res = frappe.db.sql(
			"""
            SELECT exchange_rate
            FROM `tabCurrency Exchange`
            WHERE from_currency = %s
              AND to_currency = %s
              AND date <= %s
            ORDER BY date DESC
            LIMIT 1
            """,
			(from_currency, to_currency, on_date),
			as_dict=True,
		)
		if res and res[0].get("exchange_rate"):
			return float(res[0]["exchange_rate"])
		return None

	base_amount = flt(amount)

	if payable_currency == company_currency:
		pay_amt = base_amount
		pay_ex = 1
	else:
		r = _fx_rate(payable_currency, company_currency, posting_date) or 1
		pay_amt = base_amount / float(r)
		pay_ex = float(r)

	if equity_currency == company_currency:
		eq_amt = base_amount
		eq_ex = 1
	else:
		r = _fx_rate(equity_currency, company_currency, posting_date) or 1
		eq_amt = base_amount / float(r)
		eq_ex = float(r)

	# Debit Opening Balance Equity
	eq_debit = {
		"account": equity_account,
		"debit_in_account_currency": eq_amt,
		"credit_in_account_currency": 0,
		"debit": base_amount,  # Base currency amount for GL Entry
		"credit": 0,  # Base currency amount for GL Entry
	}
	if _has_child_field("account_currency"):
		eq_debit["account_currency"] = equity_currency
	if _has_child_field("exchange_rate"):
		eq_debit["exchange_rate"] = eq_ex
	je.append("accounts", eq_debit)

	# DYNAMIC LINKING: Automatically link Payable account to Supplier
	# Check if account is already linked to the supplier
	try:
		supplier_doc = frappe.get_doc("Supplier", supplier)
		account_exists = False
		if hasattr(supplier_doc, "accounts"):
			for acc_row in supplier_doc.get("accounts", []):
				if acc_row.get("account") == payable_account and acc_row.get("company") == company:
					account_exists = True
					break

		if not account_exists:
			# Account not linked - add it dynamically
			if hasattr(supplier_doc, "accounts"):
				supplier_doc.append(
					"accounts",
					{
						"company": company,
						"account": payable_account,
					},
				)
				supplier_doc.save(ignore_permissions=True)
				_dbg(
					"create_ap_opening_journal_entry:supplier_account_linked_dynamically",
					{
						"supplier": supplier,
						"account": payable_account,
						"company": company,
						"note": f"Dynamically linked payable account '{payable_account}' to supplier '{supplier}' based on opening balance",
					},
				)
	except Exception as link_error:
		# Don't fail the journal entry if account linking fails
		_dbg(
			"create_ap_opening_journal_entry:account_linking_error",
			{
				"account": payable_account,
				"supplier": supplier,
				"error": str(link_error),
				"note": "Failed to dynamically link account to supplier, but continuing with journal entry",
			},
		)

	# Credit Payable (increase vendor balance)
	pay_row = {
		"account": payable_account,
		"party_type": "Supplier",
		"party": supplier,
		"debit_in_account_currency": 0,
		"credit_in_account_currency": pay_amt,
		"debit": 0,  # Base currency amount for GL Entry
		"credit": base_amount,  # Base currency amount for GL Entry
	}
	if _has_child_field("account_currency"):
		pay_row["account_currency"] = payable_currency
	if _has_child_field("exchange_rate"):
		pay_row["exchange_rate"] = pay_ex
	je.append("accounts", pay_row)

	try:
		je.flags.ignore_mandatory = True
		je.insert()

		if auto_submit:
			je.submit()
			_dbg("create_ap_opening_journal_entry:success", {"name": je.name, "status": "submitted"})
		else:
			_dbg("create_ap_opening_journal_entry:success", {"name": je.name, "status": "draft"})

		frappe.db.commit()  # nosemgrep
		return je.name
	except Exception as e:
		frappe.db.rollback()
		_dbg(
			"create_ap_opening_journal_entry:insert_error",
			{"error": str(e), "supplier": supplier, "amount": amount},
		)
		raise


def sync_bank_opening_balances(quickbooks_obj, company, cutoff_date, stats, auto_submit=False, cache=None):
	"""
	Sync Bank account opening balances
	Creates Journal Entry with Debit to Bank and Credit to Opening Balance Equity
	"""
	try:
		if not cache:
			from .sync_utils import SyncCache

			cache = SyncCache(company)

		# Query only Bank accounts using WHERE clause for efficiency
		# Note: QuickBooks API doesn't support comparison operators like > 0, so we filter CurrentBalance in Python
		bank_query = (
			"""SELECT Id, Name, CurrentBalance, AccountType FROM Account WHERE AccountType = 'Bank'"""
		)
		# Use pagination helper to fetch only bank accounts
		bank_accounts = query_with_pagination(
			quickbooks_obj,
			bank_query,
			"Account",
			module_name="sync_opening_balances_bank",
			data_name="bank_opening_balances",
		)
		# query_with_pagination always returns a list, but ensure it's not None
		if not bank_accounts:
			bank_accounts = []

		frappe.logger().info(f"[Opening Balances] Fetched {len(bank_accounts)} bank accounts from QuickBooks")

		# Save raw data for debugging
		try:
			save_qb_data_to_json(
				data=bank_accounts,
				data_name="bank_opening_balances",
				module_name="sync_opening_balances_bank",
				full_response={"QueryResponse": {"Account": bank_accounts}},
			)
		except Exception as e:
			_dbg("sync_bank_opening_balances:debug_save_error", {"error": str(e)})
			frappe.logger().warning(f"[Opening Balances] Failed to save debug JSON: {e!s}")

		# Filter accounts with non-zero balances
		accounts_with_balance = [acc for acc in bank_accounts if flt(acc.get("CurrentBalance", 0)) != 0]

		# Count accounts by balance type for logging
		positive_balance = [acc for acc in accounts_with_balance if flt(acc.get("CurrentBalance", 0)) > 0]
		negative_balance = [acc for acc in accounts_with_balance if flt(acc.get("CurrentBalance", 0)) < 0]

		frappe.logger().info(
			f"[Opening Balances] Bank accounts breakdown: "
			f"{len(accounts_with_balance)} with non-zero balance "
			f"({len(positive_balance)} positive, {len(negative_balance)} negative) "
			f"out of {len(bank_accounts)} total bank accounts"
		)

		# Log summary only if there are accounts to process
		if accounts_with_balance:
			qb_log_status(
				title=_("Found {0} bank accounts with non-zero balances").format(len(accounts_with_balance)),
				method="sync_bank_opening_balances",
				status="Success",
				message=_(
					"Processing {0} bank accounts ({1} positive, {2} negative) out of {3} total bank accounts"
				).format(
					len(accounts_with_balance),
					len(positive_balance),
					len(negative_balance),
					len(bank_accounts),
				),
				module="sync_opening_balances",
				request_data={
					"total_bank_accounts": len(bank_accounts),
					"bank_accounts_with_balance": len(accounts_with_balance),
					"positive_balance_count": len(positive_balance),
					"negative_balance_count": len(negative_balance),
				},
			)

		# Initialize generic Import Tracker
		from ..utils.import_tracker import ImportTracker

		tracker = ImportTracker(
			len(bank_accounts), "qb_bank_opening_import.log", company=company, module_name="BANK_OPENING"
		)

		for qb_account in bank_accounts:
			try:
				balance = flt(qb_account.get("CurrentBalance", 0))
				# Skip only if balance is exactly zero (allow negative balances)
				if balance == 0:
					continue

				account_id = str(qb_account.get("Id"))  # Ensure string for lookup
				account_name = qb_account.get("Name")

				# Log start of processing
				# Create unique ID for the tracker (use JE ID as requested)

				raw_je_id = f"BANK-OPEN-{account_id}"
				unique_je_id = make_unique_qb_id(raw_je_id, company)

				# Log start of processing
				tracker_id = f"{unique_je_id} ({account_name})"
				tracker.log_processing("QuickBooks", tracker_id)

				# Early check: Skip if Bank Opening Entry already exists
				qb_je_id = f"BANK-OPEN-{account_id}"

				unique_je_id = make_unique_qb_id(qb_je_id, company)

				existing_je = frappe.db.get_value(
					"Journal Entry", {"quickbooks_journal_entry_id": unique_je_id, "company": company}, "name"
				)

				if existing_je:
					stats["bank_accounts"] += 1
					tracker.log_skip("QuickBooks", tracker_id, "already_exists")
					continue

				# Find corresponding ERPNext account using unique QB ID

				unique_account_id = make_unique_qb_id(account_id, company)

				# Optimized: Use cache
				erp_account = cache.get_account_by_qb_id(unique_account_id)

				if not erp_account:
					erp_account = frappe.db.get_value(
						"Account", {"quickbooks_account_id": unique_account_id, "company": company}, "name"
					)

				# If not found by quickbooks_account_id, try by account name
				if not erp_account:
					erp_account = frappe.db.get_value(
						"Account", {"account_name": account_name, "company": company}, "name"
					)

				if not erp_account:
					error_msg = _(
						"Bank account '{0}' (QuickBooks ID: {1}) not found in ERPNext. "
						"Please sync accounts first or ensure the account exists with quickbooks_account_id = {1}."
					).format(account_name, account_id)

					qb_log_error(
						title=_("Bank Account Not Found: {0}").format(account_name),
						status="Error",
						method="sync_bank_opening_balances",
						message=error_msg,
						module="sync_opening_balances",
						request_data={
							"qb_id": account_id,
							"qb_name": account_name,
							"company": company,
							"balance": balance,
						},
					)

					stats["errors"] += 1
					stats["failed_accounts"].append(
						{"name": account_name, "qb_id": account_id, "type": "Bank", "balance": balance}
					)
					tracker.log_error("QuickBooks", tracker_id, "account_not_found_in_erpnext")
					continue

				# Create Journal Entry for bank opening balance
				try:
					je_name = create_bank_opening_journal_entry(
						company=company,
						bank_account=erp_account,
						account_name=account_name,
						amount=balance,
						posting_date=cutoff_date,
						quickbooks_id=account_id,
						auto_submit=auto_submit,
					)

					stats["bank_accounts"] += 1
					tracker.log_success("QuickBooks", tracker_id, "CREATED")

					# Log success
					qb_log_status(
						title=_("Created Opening Balance JE for {0}").format(account_name),
						method="sync_bank_opening_balances",
						status="Success",
						message=_("Journal Entry {0} created for bank account {1} with balance {2}").format(
							je_name, account_name, balance
						),
						module="sync_opening_balances",
						request_data={
							"je_name": je_name,
							"account": erp_account,
							"account_name": account_name,
							"amount": balance,
							"qb_id": account_id,
							"status": "submitted" if auto_submit else "draft",
						},
					)
				except Exception as je_error:
					stats["errors"] += 1
					qb_log_exception(
						method="sync_bank_opening_balances",
						err=je_error,
						request_data={
							"qb_account": qb_account,
							"erp_account": erp_account,
							"account_name": account_name,
							"balance": balance,
						},
						module="sync_opening_balances",
					)

			except Exception as e:
				_dbg("sync_bank_opening_balances:error", {"account": qb_account.get("Name"), "error": str(e)})
				frappe.logger().error(
					f"[Opening Balances] Error processing account {qb_account.get('Name')}: {e!s}"
				)
				stats["errors"] += 1
				qb_log_exception(
					method="sync_bank_opening_balances",
					err=e,
					request_data=qb_account,
					module="sync_opening_balances",
				)
				tracker.log_error("QuickBooks", tracker_id, str(e))

	except Exception as e:
		_dbg("sync_bank_opening_balances:query_error", {"error": str(e)})
		frappe.logger().error(f"[Opening Balances] Query error in bank sync: {e!s}")
		qb_log_exception(
			method="sync_bank_opening_balances",
			err=e,
			request_data={},
			module="sync_opening_balances",
		)

	# Log final summary
	tracker.log_summary()


def create_bank_opening_journal_entry(
	company, bank_account, account_name, amount, posting_date, quickbooks_id, auto_submit=False
):
	"""Create Journal Entry for bank opening balance"""

	# Check if already created
	qb_je_id = f"BANK-OPEN-{quickbooks_id}"
	existing = frappe.db.get_value(
		"Journal Entry", {"quickbooks_journal_entry_id": qb_je_id, "company": company}, "name"
	)

	if existing:
		_dbg(
			"create_bank_opening_journal_entry:exists",
			{"name": existing, "qb_je_id": qb_je_id, "account": bank_account},
		)
		frappe.logger().info(f"[Opening Balances] JE already exists: {existing} for account {account_name}")
		return existing

	_dbg(
		"create_bank_opening_journal_entry:creating",
		{
			"company": company,
			"bank_account": bank_account,
			"account_name": account_name,
			"amount": amount,
			"qb_je_id": qb_je_id,
		},
	)
	frappe.logger().info(
		f"[Opening Balances] Creating JE for {account_name} (Account: {bank_account}, Amount: {amount})"
	)

	# Get Opening Balance Equity account
	equity_account = get_or_create_opening_balance_equity_account(company)

	# Create Journal Entry
	from ..utils.qb_id_utils import make_unique_qb_id

	je = frappe.new_doc("Journal Entry")
	je.quickbooks_journal_entry_id = make_unique_qb_id(qb_je_id, company)
	je.voucher_type = "Opening Entry"
	je.naming_series = "JE-OB-BANK-"  # Bank-specific naming series
	je.posting_date = posting_date
	je.company = company
	je.multi_currency = 1  # Enable multi-currency to avoid validation errors
	je.user_remark = f"Opening Bank Balance for {account_name} (from QuickBooks)"

	if amount > 0:
		# Debit Bank
		je.append(
			"accounts",
			{
				"account": bank_account,
				"debit_in_account_currency": amount,
				"credit_in_account_currency": 0,
				"debit": flt(amount),  # Base currency amount for GL Entry
				"credit": 0,  # Base currency amount for GL Entry
			},
		)

		# Credit Opening Balance Equity
		je.append(
			"accounts",
			{
				"account": equity_account,
				"debit_in_account_currency": 0,
				"credit_in_account_currency": amount,
				"debit": 0,  # Base currency amount for GL Entry
				"credit": flt(amount),  # Base currency amount for GL Entry
			},
		)
	else:
		# For negative balances (overdrafts)
		# Credit Bank
		je.append(
			"accounts",
			{
				"account": bank_account,
				"debit_in_account_currency": 0,
				"credit_in_account_currency": abs(amount),
				"debit": 0,  # Base currency amount for GL Entry
				"credit": flt(abs(amount)),  # Base currency amount for GL Entry
			},
		)

		# Debit Opening Balance Equity
		je.append(
			"accounts",
			{
				"account": equity_account,
				"debit_in_account_currency": abs(amount),
				"credit_in_account_currency": 0,
				"debit": flt(abs(amount)),  # Base currency amount for GL Entry
				"credit": 0,  # Base currency amount for GL Entry
			},
		)

	try:
		je.flags.ignore_mandatory = True
		je.insert()

		_dbg(
			"create_bank_opening_journal_entry:inserted",
			{
				"name": je.name,
				"accounts_count": len(je.accounts),
				"total_debit": sum([flt(acc.debit_in_account_currency) for acc in je.accounts]),
				"total_credit": sum([flt(acc.credit_in_account_currency) for acc in je.accounts]),
			},
		)
		frappe.logger().info(f"[Opening Balances] JE {je.name} inserted with {len(je.accounts)} account(s)")

		if auto_submit:
			je.submit()
			_dbg(
				"create_bank_opening_journal_entry:success",
				{"name": je.name, "status": "submitted", "docstatus": je.docstatus},
			)
			frappe.logger().info(
				f"[Opening Balances] ✓ JE {je.name} submitted successfully for {account_name}"
			)
		else:
			_dbg(
				"create_bank_opening_journal_entry:success",
				{"name": je.name, "status": "draft", "docstatus": je.docstatus},
			)
			frappe.logger().info(f"[Opening Balances] ✓ JE {je.name} created as draft for {account_name}")

		frappe.db.commit()  # nosemgrep
		return je.name
	except Exception as e:
		frappe.db.rollback()
		_dbg(
			"create_bank_opening_journal_entry:insert_error",
			{
				"error": str(e),
				"error_type": type(e).__name__,
				"bank_account": bank_account,
				"account_name": account_name,
				"amount": amount,
				"company": company,
				"posting_date": posting_date,
				"equity_account": equity_account,
			},
		)
		frappe.logger().error(
			f"[Opening Balances] ✗ Failed to create JE for {account_name}: {type(e).__name__}: {e!s}"
		)
		raise


def sync_loan_opening_balances(quickbooks_obj, company, cutoff_date, stats, auto_submit=False, cache=None):
	"""
	Sync Loan account opening balances
	Creates Journal Entry with Debit to Opening Balance Equity and Credit to Loan (Liability)
	"""
	try:
		if not cache:
			from .sync_utils import SyncCache

			cache = SyncCache(company)

		# Query liability accounts - QuickBooks API doesn't support OR in WHERE clause
		# Also doesn't support comparison operators like > 0, so we filter CurrentBalance in Python
		# So we need to make two separate queries and combine results
		long_term_query = """SELECT Id, Name, CurrentBalance, AccountType FROM Account WHERE AccountType = 'Long Term Liability'"""
		other_liability_query = """SELECT Id, Name, CurrentBalance, AccountType FROM Account WHERE AccountType = 'Other Current Liability'"""

		# Fetch both types separately
		long_term_accounts = query_with_pagination(
			quickbooks_obj,
			long_term_query,
			"Account",
			module_name="sync_opening_balances_loan",
			data_name="loan_opening_balances_long_term",
		)
		other_liability_accounts = query_with_pagination(
			quickbooks_obj,
			other_liability_query,
			"Account",
			module_name="sync_opening_balances_loan",
			data_name="loan_opening_balances_other",
		)

		# Combine results
		loan_accounts = []
		if long_term_accounts:
			loan_accounts.extend(long_term_accounts)
		if other_liability_accounts:
			loan_accounts.extend(other_liability_accounts)

		frappe.logger().info(
			f"[Opening Balances] Fetched {len(loan_accounts)} liability accounts from QuickBooks "
			f"({len(long_term_accounts) if long_term_accounts else 0} Long Term, "
			f"{len(other_liability_accounts) if other_liability_accounts else 0} Other Current)"
		)

		# Save raw data for debugging
		try:
			save_qb_data_to_json(
				data=loan_accounts,
				data_name="loan_opening_balances",
				module_name="sync_opening_balances_loan",
				full_response={"QueryResponse": {"Account": loan_accounts}},
			)
		except Exception as e:
			_dbg("sync_loan_opening_balances:debug_save_error", {"error": str(e)})

		_dbg("sync_loan_opening_balances:fetched", {"count": len(loan_accounts)})

		# Initialize generic Import Tracker
		from ..utils.import_tracker import ImportTracker

		tracker = ImportTracker(
			len(loan_accounts), "qb_loan_opening_import.log", company=company, module_name="LOAN_OPENING"
		)

		for qb_account in loan_accounts:
			try:
				balance = flt(qb_account.get("CurrentBalance", 0))
				# For liability accounts, positive balance means we owe money
				# Skip only if balance is zero
				if balance == 0:
					continue

				# Use absolute value to handle any negative balances
				balance = abs(balance)

				account_id = qb_account.get("Id")
				account_name = qb_account.get("Name")

				# Log start of processing
				# Create unique ID for the tracker (use JE ID as requested)

				raw_je_id = f"LOAN-OPEN-{account_id}"
				unique_je_id = make_unique_qb_id(raw_je_id, company)

				tracker_id = f"{unique_je_id} ({account_name})"
				tracker.log_processing("QuickBooks", tracker_id)

				# Early check: Skip if Loan Opening Entry already exists
				qb_je_id = f"LOAN-OPEN-{account_id}"

				unique_je_id = make_unique_qb_id(qb_je_id, company)

				existing_je = frappe.db.get_value(
					"Journal Entry", {"quickbooks_journal_entry_id": unique_je_id}, "name"
				)

				if existing_je:
					stats["loan_accounts"] += 1
					tracker.log_skip("QuickBooks", tracker_id, "already_exists")
					continue

				# Find corresponding ERPNext account using unique QB ID

				unique_account_id = make_unique_qb_id(str(account_id), company)

				# Optimized: Use cache
				erp_account = cache.get_account_by_qb_id(unique_account_id)

				if not erp_account:
					erp_account = frappe.db.get_value(
						"Account", {"quickbooks_account_id": unique_account_id, "company": company}, "name"
					)

				if not erp_account:
					error_msg = _(
						"Loan account '{0}' (QuickBooks ID: {1}) not found in ERPNext. "
						"Please sync accounts first or ensure the account exists with quickbooks_account_id = {1}."
					).format(account_name, account_id)

					_dbg(
						"sync_loan_opening_balances:account_not_found",
						{"qb_id": account_id, "qb_name": account_name, "company": company},
					)

					qb_log_error(
						title=_("Loan Account Not Found"),
						status="Error",
						method="sync_loan_opening_balances",
						message=error_msg,
						module="sync_opening_balances",
					)

					stats["errors"] += 1
					stats["failed_accounts"].append(
						{"name": account_name, "qb_id": account_id, "type": "Loan"}
					)
					tracker.log_error("QuickBooks", tracker_id, "account_not_found_in_erpnext")
					continue

				# Create Journal Entry for loan opening balance
				create_loan_opening_journal_entry(
					company=company,
					loan_account=erp_account,
					account_name=account_name,
					amount=balance,
					posting_date=cutoff_date,
					quickbooks_id=account_id,
					auto_submit=auto_submit,
				)

				stats["loan_accounts"] += 1
				tracker.log_success("QuickBooks", tracker_id, "CREATED")
				_dbg("sync_loan_opening_balances:created", {"account": erp_account, "amount": balance})

			except Exception as e:
				_dbg("sync_loan_opening_balances:error", {"account": qb_account.get("Name"), "error": str(e)})
				stats["errors"] += 1
				qb_log_exception(
					method="sync_loan_opening_balances",
					err=e,
					request_data=qb_account,
					module="sync_opening_balances",
				)
				tracker.log_error("QuickBooks", tracker_id, str(e))

	except Exception as e:
		_dbg("sync_loan_opening_balances:query_error", {"error": str(e)})
		qb_log_exception(
			method="sync_loan_opening_balances",
			err=e,
			request_data={},
			module="sync_opening_balances",
		)

	# Log final summary
	tracker.log_summary()


def create_loan_opening_journal_entry(
	company, loan_account, account_name, amount, posting_date, quickbooks_id, auto_submit=False
):
	"""Create Journal Entry for loan opening balance"""

	# Check if already created
	qb_je_id = f"LOAN-OPEN-{quickbooks_id}"
	existing = frappe.db.get_value("Journal Entry", {"quickbooks_journal_entry_id": qb_je_id}, "name")

	if existing:
		_dbg("create_loan_opening_journal_entry:exists", {"name": existing})
		return existing

	# Ensure fiscal year exists for the posting date
	if company and posting_date:
		ensure_fiscal_year_for_date(company, posting_date, module_name="sync_opening_balances_loan")

	# Get Opening Balance Equity account
	equity_account = get_or_create_opening_balance_equity_account(company)

	# Create Journal Entry
	from ..utils.qb_id_utils import make_unique_qb_id

	je = frappe.new_doc("Journal Entry")
	je.quickbooks_journal_entry_id = make_unique_qb_id(qb_je_id, company)
	je.voucher_type = "Opening Entry"
	je.naming_series = "JE-OB-LOAN-"  # Loan-specific naming series
	je.posting_date = posting_date
	je.company = company
	je.multi_currency = 1  # Enable multi-currency to avoid validation errors
	je.user_remark = f"Opening Loan Balance for {account_name} (from QuickBooks)"

	# Debit Opening Balance Equity (to balance the liability)
	je.append(
		"accounts",
		{
			"account": equity_account,
			"debit_in_account_currency": amount,
			"credit_in_account_currency": 0,
			"debit": flt(amount),  # Base currency amount for GL Entry
			"credit": 0,  # Base currency amount for GL Entry
		},
	)

	# Credit Loan/Liability (increase liability)
	je.append(
		"accounts",
		{
			"account": loan_account,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": amount,
			"debit": 0,  # Base currency amount for GL Entry
			"credit": flt(amount),  # Base currency amount for GL Entry
		},
	)

	try:
		je.flags.ignore_mandatory = True
		je.insert()

		if auto_submit:
			je.submit()
			_dbg("create_loan_opening_journal_entry:success", {"name": je.name, "status": "submitted"})
		else:
			_dbg("create_loan_opening_journal_entry:success", {"name": je.name, "status": "draft"})

		frappe.db.commit()  # nosemgrep
		return je.name
	except Exception as e:
		frappe.db.rollback()
		_dbg(
			"create_loan_opening_journal_entry:insert_error",
			{"error": str(e), "loan_account": loan_account, "amount": amount},
		)
		raise


@frappe.whitelist()
def process_inventory_opening_chunk(
	inventory_list: list | str,
	qb_company: str | None = None,
	cutoff_date: str | None = None,
	auto_submit: bool | int | str = False,
):
	"""Background job to process a chunk of Inventory Opening Balances (Items)"""
	try:
		from quickbooks_master_sync.quickbooks_master_sync.api import _create_quickbooks_client

		quickbooks_settings = frappe.get_doc("Quickbooks Settings", "Quickbooks Settings")
		quickbooks_obj = _create_quickbooks_client(quickbooks_settings)

		qb_company = _get_quickbooks_company(quickbooks_obj=quickbooks_obj)

		# Process the chunk
		stats = sync_qb_inventory_opening_chunk(
			inventory_list, quickbooks_obj, qb_company, cutoff_date, auto_submit
		)

		# Log summary for this chunk
		chunk_summary_msg = f"""
Inventory Opening Chunk Sync Summary:
- Total in Chunk: {len(inventory_list)}
- Successfully Processed: {stats.get('inventory_items', 0)}
- Errors: {stats.get('errors', 0)}
"""

		qb_log_status(
			title=_("✅ Inventory Opening Chunk Processed"),
			status="Success",
			method="process_inventory_opening_chunk",
			message=chunk_summary_msg,
			module="sync_opening_balances",
			request_data=stats,
		)

	except Exception as e:
		qb_log_exception(
			method="process_inventory_opening_chunk", err=e, message=str(e), module="sync_opening_balances"
		)


def sync_qb_inventory_opening_chunk(inventory_items, quickbooks_obj, company, cutoff_date, auto_submit=False):
	stats = {"inventory_items": 0, "errors": 0}

	# Performance optimization: Batch commits every 100 entries
	commit_interval = 100
	last_commit_count = 0

	from .sync_utils import SyncCache

	SyncCache(company)

	# Initialize generic Import Tracker
	from ..utils.import_tracker import ImportTracker

	tracker = ImportTracker(
		len(inventory_items),
		"qb_inventory_opening_import.log",
		company=company,
		module_name="INVENTORY_OPENING",
	)

	# Get default warehouse (try multiple approaches)
	default_warehouse = None

	# Try to get from Company settings (if custom field exists)
	try:
		from ..utils.multi_company_utils import get_warehouse_for_company

		default_warehouse = get_warehouse_for_company(company)
	except Exception as e:
		_dbg("sync_qb_inventory_opening_chunk:get_warehouse_error", {"error": str(e)})
		default_warehouse = None

	# If still not found, get any non-group warehouse for this company
	if not default_warehouse:
		default_warehouse = frappe.db.get_value(
			"Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name", order_by="creation"
		)

	if not default_warehouse:
		_dbg("sync_qb_inventory_opening_chunk:no_warehouse", {"company": company})
		frappe.log_error(f"No warehouse found for company {company}. Skipping inventory sync chunk.")
		return stats

	for qb_item in inventory_items:
		try:
			qty = flt(qb_item.get("QtyOnHand", 0))
			if qty <= 0:
				continue

			item_id = qb_item.get("Id")
			item_name = qb_item.get("Name")

			# Log start of processing
			# Create unique ID for the tracker (use Stock Recon ID as requested)

			raw_sr_id = f"SR-OPEN-{item_id}"
			unique_sr_id = make_unique_qb_id(raw_sr_id, company)

			# Log start of processing
			tracker_id = f"{unique_sr_id} ({item_name})"
			tracker.log_processing("QuickBooks", tracker_id)

			purchase_cost = flt(qb_item.get("PurchaseCost", 0))
			is_sparse = qb_item.get("sparse", False)

			# Find corresponding ERPNext item using unique QB ID
			unique_item_id = make_unique_qb_id(str(item_id), company)
			erp_item = frappe.db.get_value("Item", {"quickbooks_item_id": unique_item_id}, "name")

			if not erp_item:
				_dbg(
					"sync_inventory_opening_balances:item_not_found", {"qb_id": item_id, "qb_name": item_name}
				)
				stats["errors"] += 1
				tracker.log_error("QuickBooks", tracker_id, "item_not_found_in_erpnext")
				continue

			# Early check: Skip if Stock Reconciliation already exists
			existing_sr = frappe.db.sql(
				"""
                SELECT name FROM `tabStock Reconciliation`
                WHERE company = %s
                AND posting_date = %s
                AND docstatus = 1
                AND EXISTS (
                    SELECT 1 FROM `tabStock Reconciliation Item`
                    WHERE parent = `tabStock Reconciliation`.name
                    AND item_code = %s
                    AND warehouse = %s
                )
                LIMIT 1
            """,
				(company, cutoff_date, erp_item, default_warehouse),
			)

			if existing_sr:
				stats["inventory_items"] += 1
				tracker.log_skip("QuickBooks", tracker_id, "already_exists")
				continue

			# Get valuation rate: use PurchaseCost if available
			valuation_rate = None

			if purchase_cost > 0:
				valuation_rate = purchase_cost
			elif is_sparse and not purchase_cost and qb_item.get("class"):  # Only fetch if needed
				# Note: In worker context, fetching single item again might be expensive if many failures
				# Ideally we should pass full item data. For now, assume PurchaseCost is populated.
				pass

			# Fallback 1: Try to get from Item Price (Buying)
			if not valuation_rate or valuation_rate == 0:
				try:
					# Get buying price list using company-specific utility (with fallback to global)
					from ..utils.multi_company_utils import get_buying_price_list_for_company

					buying_price_list = get_buying_price_list_for_company(company)

					if buying_price_list:
						buying_price = frappe.db.get_value(
							"Item Price",
							{"item_code": erp_item, "buying": 1, "price_list": buying_price_list},
							"price_list_rate",
							order_by="valid_from desc",
						)
						if buying_price and buying_price > 0:
							valuation_rate = buying_price

					if not valuation_rate or valuation_rate == 0:
						buying_price = frappe.db.get_value(
							"Item Price",
							{"item_code": erp_item, "buying": 1},
							"price_list_rate",
							order_by="valid_from desc",
						)
						if buying_price and buying_price > 0:
							valuation_rate = buying_price
				except Exception as e:
					_dbg(
						"sync_inventory_opening_balances:get_buying_price_failed",
						{"erp_item": erp_item, "error": str(e)},
					)

			# Fallback 2: Try to get rates from ERPNext item
			if not valuation_rate or valuation_rate == 0:
				try:
					item_rates = frappe.db.get_value(
						"Item",
						erp_item,
						["last_purchase_rate", "standard_rate", "valuation_rate"],
						as_dict=True,
					)
					valuation_rate = (
						item_rates.get("last_purchase_rate")
						or item_rates.get("standard_rate")
						or item_rates.get("valuation_rate")
						or 0
					)
				except Exception as e:
					_dbg(
						"sync_inventory_opening_balances:get_item_rates_failed",
						{"erp_item": erp_item, "error": str(e)},
					)
					valuation_rate = 0

			if not valuation_rate or valuation_rate == 0:
				_dbg(
					"sync_inventory_opening_balances:no_valuation_rate_skipping",
					{"qb_id": item_id, "item_name": item_name},
				)
				# Log non-exception error
				qb_log_status(
					title=_("Item Skipped - No Valuation Rate"),
					status="Error",
					method="sync_inventory_opening_balances",
					message=_("Item {0} ({1}) skipped because no valuation rate could be found.").format(
						item_name, erp_item
					),
					module="sync_opening_balances",
					exception=False,
				)
				stats["errors"] += 1
				continue

			if not valuation_rate or flt(valuation_rate) <= 0:
				stats["errors"] += 1
				continue

			# Create Stock Reconciliation for opening stock
			create_opening_stock_reconciliation(
				company=company,
				item_code=erp_item,
				item_name=item_name,
				qty=qty,
				warehouse=default_warehouse,
				posting_date=cutoff_date,
				quickbooks_id=item_id,
				valuation_rate=flt(valuation_rate),
				auto_submit=auto_submit,
			)

			stats["inventory_items"] += 1
			tracker.log_success("QuickBooks", tracker_id, "CREATED")
			_dbg("sync_inventory_opening_balances:created", {"item": erp_item, "qty": qty})

		except Exception as e:
			_dbg("sync_inventory_opening_balances:error", {"item": qb_item.get("Name"), "error": str(e)})
			stats["errors"] += 1
			qb_log_exception(
				method="sync_inventory_opening_balances",
				err=e,
				request_data=qb_item,
				module="sync_opening_balances",
			)
			tracker.log_error("QuickBooks", tracker_id, str(e))

		# Batch commit every N entries for performance
		current_processed = stats["inventory_items"] + stats["errors"]
		if current_processed > 0 and (current_processed - last_commit_count) >= commit_interval:
			frappe.db.commit()  # nosemgrep
			last_commit_count = current_processed

	# Final commit
	if stats["inventory_items"] > 0 or stats["errors"] > 0:
		frappe.db.commit()  # nosemgrep

	# Log final summary
	tracker.log_summary()
	return stats


def sync_inventory_opening_balances(
	quickbooks_obj, company, cutoff_date, stats, auto_submit=False, cache=None
):
	"""
	Sync Inventory opening balances (quantity and value)
	Creates Stock Reconciliation for inventory items
	"""
	try:
		# Query all items and filter for inventory type
		# Note: QuickBooks API doesn't support comparison operators like > 0, so we filter QtyOnHand in Python
		item_query = """SELECT Id, Name, QtyOnHand, InvStartDate, Type, PurchaseCost FROM Item"""
		# Use pagination helper to fetch all items
		all_items = query_with_pagination(
			quickbooks_obj,
			item_query,
			"Item",
			module_name="sync_opening_balances_inventory",
			data_name="inventory_opening_balances",
		)
		# query_with_pagination always returns a list, but ensure it's not None
		if not all_items:
			all_items = []

		# Filter for Inventory items in Python
		inventory_items = [item for item in all_items if item.get("Type") == "Inventory"]

		# Save raw data for debugging
		try:
			save_qb_data_to_json(
				data=inventory_items,
				data_name="inventory_opening_balances",
				module_name="sync_opening_balances_inventory",
				full_response={"QueryResponse": {"Item": all_items}},
			)
		except Exception as e:
			_dbg("sync_inventory_opening_balances:debug_save_error", {"error": str(e)})

		_dbg("sync_inventory_opening_balances:fetched", {"count": len(inventory_items)})

		# Chunk the inventory list into batches of 100
		chunk_size = 100
		total_items = len(inventory_items)

		from frappe.utils.background_jobs import enqueue

		for i in range(0, total_items, chunk_size):
			chunk = inventory_items[i : i + chunk_size]
			enqueue(
				method="quickbooks_master_sync.quickbooks_master_sync.sync.sync_opening_balances.process_inventory_opening_chunk",
				queue="long",
				timeout=3600,
				inventory_list=chunk,
				qb_company=company,
				cutoff_date=cutoff_date,
				auto_submit=auto_submit,
			)

		# Return immediately as processing is done in background
		qb_log_status(
			title=_("Inventory Opening Balance Sync Enqueued"),
			status="Queued",
			method="sync_inventory_opening_balances",
			message=_("Enqueued {0} inventory items in chunks of {1} for background processing.").format(
				total_items, chunk_size
			),
			module="sync_opening_balances",
		)

	except Exception as e:
		_dbg("sync_inventory_opening_balances:query_error", {"error": str(e)})
		qb_log_exception(
			method="sync_inventory_opening_balances",
			err=e,
			request_data={},
			module="sync_opening_balances",
		)


def create_opening_stock_reconciliation(
	company,
	item_code,
	item_name,
	qty,
	warehouse,
	posting_date,
	quickbooks_id,
	valuation_rate=None,
	auto_submit=False,
):
	"""Create Stock Reconciliation for opening inventory"""

	# Check if already created

	# Check using custom field or remarks
	existing = frappe.db.sql(
		"""
        SELECT name FROM `tabStock Reconciliation`
        WHERE company = %s
        AND posting_date = %s
        AND docstatus = 1
        AND EXISTS (
            SELECT 1 FROM `tabStock Reconciliation Item`
            WHERE parent = `tabStock Reconciliation`.name
            AND item_code = %s
            AND warehouse = %s
        )
        LIMIT 1
    """,
		(company, posting_date, item_code, warehouse),
	)

	if existing:
		_dbg("create_opening_stock_reconciliation:exists", {"name": existing[0][0]})
		return existing[0][0]

	# Get or create difference account for opening stock
	difference_account = get_or_create_stock_adjustment_account(company)

	# Determine valuation rate if not provided or is 0
	if valuation_rate is None or valuation_rate == 0:
		# Try to get from Item Price (Buying) first - this is where PurchaseCost is stored
		try:
			# Get buying price list using company-specific utility (with fallback to global)
			buying_price_list = get_buying_price_list_for_company(company)

			if buying_price_list:
				buying_price = frappe.db.get_value(
					"Item Price",
					{"item_code": item_code, "buying": 1, "price_list": buying_price_list},
					"price_list_rate",
					order_by="valid_from desc",
				)
				if buying_price and buying_price > 0:
					valuation_rate = buying_price

			# If not found with price list, try any buying price
			if not valuation_rate or valuation_rate == 0:
				buying_price = frappe.db.get_value(
					"Item Price",
					{"item_code": item_code, "buying": 1},
					"price_list_rate",
					order_by="valid_from desc",
				)
				if buying_price and buying_price > 0:
					valuation_rate = buying_price
		except Exception as e:
			_dbg(
				"create_opening_stock_reconciliation:get_buying_price_failed",
				{"item_code": item_code, "error": str(e)},
			)

		# Fallback: Try to get rates from Item
		if not valuation_rate or valuation_rate == 0:
			try:
				item_rates = frappe.db.get_value(
					"Item", item_code, ["last_purchase_rate", "standard_rate", "valuation_rate"], as_dict=True
				)
				valuation_rate = (
					item_rates.get("last_purchase_rate")
					or item_rates.get("standard_rate")
					or item_rates.get("valuation_rate")
					or 0
				)
			except Exception as e:
				_dbg(
					"create_opening_stock_reconciliation:get_item_rates_failed",
					{"item_code": item_code, "error": str(e)},
				)
				valuation_rate = 0

	# Validate valuation rate - ERPNext requires it to be > 0
	valuation_rate = flt(valuation_rate)
	if not valuation_rate or valuation_rate <= 0:
		error_msg = _(
			"Valuation Rate required for Item {0} at row 1. "
			"Please set PurchaseCost in QuickBooks or Item Price (Buying) in ERPNext."
		).format(item_code)
		_dbg(
			"create_opening_stock_reconciliation:no_valuation_rate",
			{
				"item_code": item_code,
				"item_name": item_name,
				"qb_id": quickbooks_id,
				"valuation_rate": valuation_rate,
			},
		)
		frappe.throw(error_msg, title=_("Valuation Rate Required"))

	# Log the valuation rate being used
	_dbg(
		"create_opening_stock_reconciliation:using_valuation_rate",
		{
			"item_code": item_code,
			"item_name": item_name,
			"qb_id": quickbooks_id,
			"valuation_rate": valuation_rate,
		},
	)

	# Ensure fiscal year exists for the posting date
	if company and posting_date:
		ensure_fiscal_year_for_date(company, posting_date, module_name="sync_opening_balances_inventory")

	# Create Stock Reconciliation
	sr = frappe.new_doc("Stock Reconciliation")
	sr.company = company
	sr.purpose = "Opening Stock"
	sr.posting_date = posting_date
	sr.posting_time = "00:00:01"
	sr.expense_account = difference_account  # Required for Opening Stock

	# Add item with validated valuation rate
	item_row = sr.append(
		"items",
		{
			"item_code": item_code,
			"warehouse": warehouse,
			"qty": qty,
			"valuation_rate": valuation_rate,  # Already validated to be > 0
		},
	)

	# Double-check the valuation_rate was set correctly
	if not item_row.valuation_rate or flt(item_row.valuation_rate) <= 0:
		error_msg = _(
			"Failed to set Valuation Rate for Item {0}. "
			"Valuation Rate is required and must be greater than 0."
		).format(item_code)
		_dbg(
			"create_opening_stock_reconciliation:valuation_rate_not_set",
			{
				"item_code": item_code,
				"item_name": item_name,
				"qb_id": quickbooks_id,
				"valuation_rate": valuation_rate,
				"item_row_valuation_rate": item_row.valuation_rate,
			},
		)
		frappe.throw(error_msg, title=_("Valuation Rate Required"))

	try:
		sr.flags.ignore_mandatory = True
		sr.insert()

		if auto_submit:
			sr.submit()
			_dbg("create_opening_stock_reconciliation:success", {"name": sr.name, "status": "submitted"})
		else:
			_dbg("create_opening_stock_reconciliation:success", {"name": sr.name, "status": "draft"})

		frappe.db.commit()  # nosemgrep
		return sr.name
	except Exception as e:
		frappe.db.rollback()
		_dbg(
			"create_opening_stock_reconciliation:insert_error",
			{"error": str(e), "item_code": item_code, "qty": qty},
		)
		raise


def create_equity_balancing_entry(company, cutoff_date, stats):
	"""
	Create equity balancing entry to ensure books balance
	This is the final entry that balances all opening balance entries
	"""
	try:
		_dbg("create_equity_balancing_entry:start")

		# Get Opening Balance Equity account
		equity_account = get_or_create_opening_balance_equity_account(company)

		# Calculate the balance of the equity account
		balance = frappe.db.sql(
			"""
            SELECT SUM(debit) - SUM(credit) as balance
            FROM `tabGL Entry`
            WHERE account = %s
            AND company = %s
            AND posting_date <= %s
            AND is_cancelled = 0
        """,
			(equity_account, company, cutoff_date),
			as_dict=True,
		)

		equity_balance = flt(balance[0].balance if balance else 0)

		_dbg("create_equity_balancing_entry:balance", {"balance": equity_balance})

		# If equity account has a balance, create balancing entry
		# Usually this shouldn't be needed if all entries are correct
		# But this ensures the books balance

		if abs(equity_balance) < 0.01:
			_dbg("create_equity_balancing_entry:balanced", {"message": "Already balanced"})
			return

		# Note: In practice, if everything is done correctly, the Opening Balance Equity
		# should already be balanced. This function is here for completeness.
		# You may want to just log the balance for verification

		_dbg(
			"create_equity_balancing_entry:complete",
			{
				"equity_balance": equity_balance,
				"message": "Opening Balance Equity account balance for verification",
			},
		)

	except Exception as e:
		_dbg("create_equity_balancing_entry:error", {"error": str(e)})
		qb_log_exception(
			method="create_equity_balancing_entry",
			err=e,
			request_data={},
			module="sync_opening_balances",
		)


def get_or_create_opening_balance_equity_account(company):
	"""Get or create Opening Balance Equity account"""

	# Try to find existing Opening Balance Equity account
	equity_account = frappe.db.get_value(
		"Account",
		{"company": company, "account_name": "Opening Balance Equity", "account_type": "Equity"},
		"name",
	)

	if equity_account:
		return equity_account

	# Create if doesn't exist
	try:
		# Get parent equity account
		parent_account = frappe.db.get_value(
			"Account", {"company": company, "root_type": "Equity", "is_group": 1}, "name"
		)

		if not parent_account:
			frappe.throw(_("No Equity parent account found for company {0}").format(company))

		account = frappe.new_doc("Account")
		account.account_name = "Opening Balance Equity"
		account.company = company
		account.root_type = "Equity"
		account.account_type = "Equity"
		account.parent_account = parent_account
		account.is_group = 0
		account.insert()
		frappe.db.commit()  # nosemgrep

		_dbg("get_or_create_opening_balance_equity_account:created", {"account": account.name})
		return account.name

	except Exception as e:
		_dbg("get_or_create_opening_balance_equity_account:error", {"error": str(e)})
		qb_log_exception(
			method="get_or_create_opening_balance_equity_account",
			err=e,
			request_data={"company": company},
			module="sync_opening_balances",
		)
		raise


def get_or_create_stock_adjustment_account(company):
	"""Get or create account for opening stock reconciliation difference"""

	# First, try to find "Temporary Opening Stock" account
	temp_account = frappe.db.get_value(
		"Account", {"company": company, "account_name": "Temporary Opening Stock"}, "name"
	)

	if temp_account:
		return temp_account

	# Try to find existing Stock Adjustment account
	stock_adjustment_account = frappe.db.get_value(
		"Account", {"company": company, "account_name": "Stock Adjustment"}, "name"
	)

	if stock_adjustment_account:
		# Check if it's Asset or Liability type
		account_root = frappe.db.get_value("Account", stock_adjustment_account, "root_type")
		if account_root in ["Asset", "Liability"]:
			return stock_adjustment_account

	# Try to get stock adjustment account from Company settings
	company_stock_adj = frappe.db.get_value("Company", company, "stock_adjustment_account")
	if company_stock_adj:
		# Verify it's Asset or Liability
		account_root = frappe.db.get_value("Account", company_stock_adj, "root_type")
		if account_root in ["Asset", "Liability"]:
			return company_stock_adj

	# If none found, create "Temporary Opening Stock" account as Asset type
	try:
		# Get parent asset account (typically under Current Assets)
		parent_account = frappe.db.get_value(
			"Account",
			{"company": company, "account_name": ["in", ["Current Assets", "Stock Assets"]], "is_group": 1},
			"name",
		)

		# If no specific parent found, get any Asset group account
		if not parent_account:
			parent_account = frappe.db.get_value(
				"Account", {"company": company, "root_type": "Asset", "is_group": 1}, "name", order_by="lft"
			)

		if not parent_account:
			frappe.throw(_("No Asset parent account found for company {0}").format(company))

		account = frappe.new_doc("Account")
		account.account_name = "Temporary Opening Stock"
		account.company = company
		account.root_type = "Asset"
		# Don't set account_type - leave it as general Asset account
		account.parent_account = parent_account
		account.is_group = 0
		account.insert()
		frappe.db.commit()  # nosemgrep

		_dbg(
			"get_or_create_stock_adjustment_account:created",
			{"account": account.name, "type": "Temporary Opening Stock (Asset)"},
		)
		return account.name

	except Exception as e:
		_dbg("get_or_create_stock_adjustment_account:error", {"error": str(e)})
		qb_log_exception(
			method="get_or_create_stock_adjustment_account",
			err=e,
			request_data={"company": company},
			module="sync_opening_balances",
		)
		raise


def sync_entry(quickbooks_obj):
	"""
	Wrapper function to maintain compatibility with existing sync flow
	This can be called from the main sync process
	"""
	_dbg("sync_entry:deprecated", {"message": "Use sync_opening_balances() instead"})
	# You can call sync_opening_balances here if needed
	# sync_opening_balances(quickbooks_obj)
