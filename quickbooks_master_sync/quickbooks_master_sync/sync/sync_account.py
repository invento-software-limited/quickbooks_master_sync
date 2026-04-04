import time

import frappe
import frappe.defaults
from frappe import _

from quickbooks_master_sync.pyqb.quickbooks.batch import batch_create
from quickbooks_master_sync.pyqb.quickbooks.objects.account import Account
from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error, qb_log_exception

from .sync_company import ensure_all_defaults
from .sync_utils import _dbg as _dbg_common
from .sync_utils import _get_quickbooks_company as _get_quickbooks_company_base
from .sync_utils import query_with_pagination, save_qb_data_to_json

"""Code to fetch all the Account from Quickbooks And store it in ERPNEXT"""


def _dbg(event, payload=None):
	"""Debug logging helper for account sync"""
	_dbg_common("sync_account", event, payload)


def get_erpnext_root_and_account_type(qb_account):
	"""
	Returns (root_type, account_type) for ERPNext based on QB AccountType and SubType.
	"""
	qb_type = qb_account.get("AccountType")
	qb_subtype = qb_account.get("AccountSubType")

	# Default assignments
	root_type = "Asset"
	account_type = ""

	# --- ASSETS ---
	if qb_type == "Bank":
		root_type, account_type = "Asset", "Bank"
		# Check SubType for "CashOnHand" explicitly
		if qb_subtype == "CashOnHand":
			account_type = "Cash"
	elif qb_type == "Accounts Receivable":
		root_type, account_type = "Asset", "Receivable"
	elif qb_type in ["Other Current Asset", "Other Asset"]:
		root_type = "Asset"
		account_type = "Current Asset"
		if qb_subtype:
			if "Stock" in qb_subtype or "Inventory" in qb_subtype:
				account_type = "Stock"
			elif qb_subtype == "AllowanceForBadDebts":
				account_type = "Receivable"  # Often used for Provision for Doubtful Debts
	elif qb_type == "Fixed Asset":
		root_type, account_type = "Asset", "Fixed Asset"
		if qb_subtype == "AccumulatedDepreciation":
			account_type = "Accumulated Depreciation"

	# --- LIABILITIES ---
	elif qb_type == "Accounts Payable":
		root_type, account_type = "Liability", "Payable"
	elif qb_type == "Credit Card":
		root_type, account_type = "Liability", "Liability"
	elif qb_type in ["Other Current Liability", "Long Term Liability"]:
		root_type = "Liability"
		account_type = "Liability"
		# Check SubType for Tax accounts
		if qb_subtype in ["GlobalTaxPayable", "PayrollTaxPayable", "SalesTaxPayable"] or (
			qb_subtype and "Tax" in qb_subtype
		):
			account_type = "Tax"

	# --- EQUITY ---
	elif qb_type == "Equity":
		root_type, account_type = "Equity", "Equity"

	# --- INCOME ---
	elif qb_type in ["Income", "Other Income"]:
		root_type = "Income"
		account_type = "Income Account" if qb_type == "Income" else "Indirect Income"
		if qb_subtype == "SalesOfProductIncome":
			account_type = "Direct Income"

	# --- EXPENSES ---
	elif qb_type == "Cost of Goods Sold":
		root_type, account_type = "Expense", "Cost of Goods Sold"
	elif qb_type in ["Expense", "Other Expense"]:
		root_type = "Expense"
		account_type = "Expense Account" if qb_type == "Expense" else "Indirect Expense"
		if qb_subtype == "CostOfSales":
			account_type = "Cost of Goods Sold"

	return root_type, account_type


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None):
	"""Wrapper for sync_account module"""
	return _get_quickbooks_company_base(
		quickbooks_obj=quickbooks_obj,
		qb_company=qb_company,
		module_name="sync_account",
	)


def _get_root_account_for_type(company, root_type):
	"""Find the root account (account with no parent) for a given root_type"""
	root_accounts = frappe.db.sql(
		"""
        SELECT name
        FROM `tabAccount`
        WHERE company = %s
        AND root_type = %s
        AND is_group = 1
        AND (parent_account IS NULL OR parent_account = '')
        ORDER BY creation ASC
        LIMIT 1
        """,
		(company, root_type),
		as_dict=1,
	)

	if root_accounts:
		return root_accounts[0].name

	return None


def _get_default_parent_account(company, root_type):
	"""
	Get the default parent account for a given root_type.
	All QuickBooks accounts will be created as children under these parent accounts.

	Args:
	    company (str): Company name
	    root_type (str): Root type (Asset, Liability, Equity, Income, Expense)

	Returns:
	    str: Parent account name
	"""
	abbr = frappe.db.get_value("Company", {"name": company}, "abbr")
	if not abbr:
		raise ValueError(f"Company '{company}' not found or has no abbreviation")

	# Map root_type to default parent account name
	parent_mapping = {
		"Asset": f"Assets - {abbr}",
		"Liability": f"Liabilities - {abbr}",
		"Equity": f"Equity - {abbr}",
		"Income": f"Income - {abbr}",
		"Expense": f"Expenses - {abbr}",
	}

	parent_account = parent_mapping.get(root_type)
	if not parent_account:
		# Fallback to Assets if root_type is unknown
		parent_account = f"Assets - {abbr}"
		_dbg("get_default_parent:unknown_root_type", {"root_type": root_type, "fallback": parent_account})

	# Ensure the parent account exists (it should be created by ERPNext during company setup)
	if not frappe.db.get_value("Account", {"name": parent_account}, "name"):
		# Try to find any root account of this type
		root_account = _get_root_account_for_type(company, root_type)
		if root_account:
			parent_account = root_account
			_dbg(
				"get_default_parent:using_root_account",
				{"root_type": root_type, "parent_account": parent_account},
			)
		else:
			# Last resort: find any account of this root_type that is a group
			fallback_parent = frappe.db.get_value(
				"Account",
				{"company": company, "root_type": root_type, "is_group": 1},
				"name",
				order_by="creation ASC",
			)
			if fallback_parent:
				parent_account = fallback_parent
				_dbg(
					"get_default_parent:using_fallback",
					{"root_type": root_type, "parent_account": parent_account},
				)
			else:
				raise ValueError(
					f"No parent account found for root_type '{root_type}' in company '{company}'. "
					f"Please ensure the company has a proper chart of accounts set up."
				)

	return parent_account


def _ensure_root_accounts(company):
	"""
	Ensure the 5 core root accounts exist for the company.
	These accounts must have no parent and be marked as groups.
	"""
	abbr = frappe.db.get_value("Company", company, "abbr")
	if not abbr:
		return

	root_map = {
		"Assets": "Asset",
		"Liabilities": "Liability",
		"Income": "Income",
		"Expenses": "Expense",
		"Equity": "Equity",
	}

	# FIRST: Clean up any broken root accounts created via direct SQL that might have lft/rgt = 0
	# This prevents NestedSet errors later.
	frappe.db.sql(
		"""
        DELETE FROM `tabAccount`
        WHERE (lft = 0 OR lft IS NULL)
        AND company = %s
        AND parent_account IS NULL
    """,
		(company,),
	)
	frappe.db.commit()

	created = []
	for label, rtype in root_map.items():
		name = f"{label} - {abbr}"

		# 1. Check if account with this name already exists (to prevent DuplicateEntryError)
		existing_root = None
		if frappe.db.exists("Account", name):
			existing_root = name
			# If it exists, ensure it's a group and a root (no parent)
			acc = frappe.get_doc("Account", name)
			modified = False
			if acc.is_group != 1:
				acc.is_group = 1
				modified = True
			if acc.parent_account:
				acc.parent_account = None
				modified = True
			if acc.root_type != rtype:
				acc.root_type = rtype
				modified = True

			if modified:
				acc.flags.ignore_mandatory = True
				acc.flags.ignore_permissions = True
				acc.save()
				_dbg("ensure_root_accounts:fixed_existing", {"name": name, "root_type": rtype})
		else:
			# 2. If name doesn't exist, check if ANY other root account for this type exists
			existing_root = frappe.db.get_value(
				"Account",
				{"company": company, "root_type": rtype, "is_group": 1, "parent_account": ["in", ["", None]]},
				"name",
			)

		if not existing_root:
			# Force create the root account using Document API but ignore mandatory parent_account
			# This ensures Nested Set (lft, rgt) columns are populated correctly.
			doc = frappe.new_doc("Account")
			doc.account_name = label
			doc.name = name
			doc.company = company
			doc.is_group = 1
			doc.root_type = rtype
			doc.parent_account = None
			doc.flags.ignore_mandatory = True
			doc.flags.ignore_permissions = True
			doc.insert()

			created.append(name)
			_dbg("ensure_root_accounts:created", {"name": name, "root_type": rtype})
		else:
			# If name is different but it is already a root, we just ensure it's a group
			if existing_root != name:
				if frappe.db.get_value("Account", existing_root, "is_group") != 1:
					frappe.db.set_value("Account", existing_root, "is_group", 1)
					_dbg("ensure_root_accounts:fixed_group_status", {"name": existing_root})

	if created:
		# Rebuild tree to ensure Nested Set integrity after forced root creation
		from frappe.utils.nestedset import rebuild_tree

		rebuild_tree("Account")
		frappe.db.commit()
		_dbg("ensure_root_accounts:summary", {"created_count": len(created), "accounts": created})


def _ensure_root_cost_centers(company):
	"""
	Ensure a base cost center hierarchy exists:
	1. Root Cost Center (Abbreviation) - Group
	2. Main Cost Center - Leaf (Child of Root)
	"""
	abbr = frappe.db.get_value("Company", company, "abbr")
	if not abbr:
		return

	# 1. Ensure Root Group exists (Search by cost_center_name + company)
	root_name = frappe.db.get_value(
		"Cost Center",
		{"cost_center_name": abbr, "company": company, "parent_cost_center": ["in", ["", None]]},
		"name",
	)

	if not root_name:
		doc = frappe.new_doc("Cost Center")
		doc.cost_center_name = abbr
		doc.company = company
		doc.is_group = 1
		doc.parent_cost_center = None
		doc.flags.ignore_mandatory = True
		doc.flags.ignore_validate = True
		try:
			doc.insert()
			root_name = doc.name
			_dbg("ensure_root_cost_centers:root_created", {"name": root_name})
		except frappe.DuplicateEntryError:
			# If creation fails due to duplicate, try to find the existing one again or construct the expected name
			root_name = f"{abbr} - {abbr}"  # Standard Naming: Name - CompanyAbbr
			if not frappe.db.exists("Cost Center", root_name):
				# Try finding by company and is_group if name assumption is wrong
				root_name = frappe.db.get_value(
					"Cost Center",
					{"company": company, "is_group": 1, "parent_cost_center": ["in", ["", None]]},
					"name",
				)
			_dbg("ensure_root_cost_centers:root_exists_recovered", {"name": root_name})
	else:
		# Ensure it's a group and has no parent
		root_doc = frappe.get_doc("Cost Center", root_name)
		modified = False
		if root_doc.is_group != 1:
			root_doc.is_group = 1
			modified = True
		if root_doc.parent_cost_center:
			root_doc.parent_cost_center = None
			modified = True

		if modified:
			root_doc.flags.ignore_mandatory = True
			root_doc.flags.ignore_validate = True
			root_doc.save()
			_dbg("ensure_root_cost_centers:root_fixed", {"name": root_name})

	# 2. Ensure "Main" Leaf exists under Root
	main_name = f"Main - {abbr}"
	if not frappe.db.exists("Cost Center", main_name):
		doc = frappe.new_doc("Cost Center")
		doc.cost_center_name = "Main"
		doc.name = main_name
		doc.company = company
		doc.is_group = 0
		doc.parent_cost_center = root_name
		doc.flags.ignore_mandatory = True
		doc.insert()
		_dbg("ensure_root_cost_centers:main_leaf_created", {"name": main_name})
	else:
		# Ensure it's a leaf and child of the actual Root name
		main_doc = frappe.get_doc("Cost Center", main_name)
		modified = False
		if main_doc.is_group != 0:
			main_doc.is_group = 0
			modified = True
		if main_doc.parent_cost_center != root_name:
			main_doc.parent_cost_center = root_name
			modified = True

		if modified:
			main_doc.flags.ignore_mandatory = True
			main_doc.save()
			_dbg("ensure_root_cost_centers:main_leaf_fixed", {"name": main_name})

	# 3. Always set "Main" leaf as default for Company
	company_doc = frappe.get_doc("Company", company)
	if not company_doc.cost_center or company_doc.cost_center != main_name:
		company_doc.cost_center = main_name
		company_doc.flags.ignore_mandatory = True
		company_doc.save()
		frappe.db.commit()
		_dbg("ensure_root_cost_centers:company_default_set", {"company": company, "cost_center": main_name})

	# Final tree rebuild to ensure integrity
	from frappe.utils.nestedset import rebuild_tree

	rebuild_tree("Cost Center")
	frappe.db.commit()


def _map_quickbooks_account_type_to_erpnext(qb_account_type):
	"""
	Map QuickBooks AccountType to ERPNext account_type.

	QuickBooks uses different account type names than ERPNext.
	This function converts QuickBooks account types to valid ERPNext account types.
	"""
	if not qb_account_type:
		return None

	# Mapping from QuickBooks AccountType to ERPNext account_type
	account_type_mapping = {
		"Accounts Receivable": "Receivable",
		"Accounts Payable": "Payable",
		"Bank": "Bank",
		"Cash": "Cash",
		"Credit Card": "Liability",  # Credit cards are liabilities
		"Fixed Asset": "Fixed Asset",
		"Other Asset": "Current Asset",
		"Other Current Asset": "Current Asset",
		"Other Current Liability": "Liability",  # Map to Liability for ledger accounts
		"Long Term Liability": "Liability",
		"GlobalTaxPayable": "Tax",  # Tax/Duty Payable accounts
		"Equity": "Equity",
		"Income": "Income Account",
		"Other Income": "Indirect Income",
		"Expense": "Expense Account",
		"Other Expense": "Indirect Expense",
		"Cost of Goods Sold": "Cost of Goods Sold",
		"Non-Posting": None,  # Non-posting accounts might not have a specific type
	}

	# Return mapped type or original if not in mapping
	mapped_type = account_type_mapping.get(qb_account_type)
	if mapped_type:
		return mapped_type

	# If not in mapping, try to use as-is (might work for some types)
	# But validate against known ERPNext types
	valid_erpnext_types = [
		"",
		"Accumulated Depreciation",
		"Asset Received But Not Billed",
		"Bank",
		"Cash",
		"Chargeable",
		"Capital Work in Progress",
		"Cost of Goods Sold",
		"Current Asset",
		"Current Liability",
		"Depreciation",
		"Direct Expense",
		"Direct Income",
		"Equity",
		"Expense Account",
		"Expenses Included In Asset Valuation",
		"Expenses Included In Valuation",
		"Fixed Asset",
		"Income Account",
		"Indirect Expense",
		"Indirect Income",
		"Liability",
		"Payable",
		"Receivable",
		"Round Off",
		"Round Off for Opening",
		"Stock",
		"Stock Adjustment",
		"Stock Received But Not Billed",
		"Service Received But Not Billed",
		"Tax",
		"Temporary",
	]

	# If the QB type is already a valid ERPNext type, use it
	if qb_account_type in valid_erpnext_types:
		return qb_account_type

	# Default fallback based on common patterns
	qb_type_lower = qb_account_type.lower()
	if "receivable" in qb_type_lower:
		return "Receivable"
	elif "payable" in qb_type_lower:
		return "Payable"
	elif "asset" in qb_type_lower:
		return "Current Asset"
	elif "liability" in qb_type_lower:
		return "Current Liability"
	elif "income" in qb_type_lower:
		return "Income Account"
	elif "expense" in qb_type_lower:
		return "Expense Account"

	# If we can't map it, return None (account_type will be empty)
	return None


def sync_account(quickbooks_obj):
	"""Fetch Account data from QuickBooks"""
	_dbg("sync_account:start")
	# Debug quickbooks_obj attributes
	try:
		qb_info = {
			"company_id": getattr(quickbooks_obj, "company_id", None),
			"has_access_token": bool(getattr(quickbooks_obj, "access_token", None)),
			"has_session": getattr(quickbooks_obj, "session", None) is not None,
			"type": type(quickbooks_obj).__name__,
		}
		_dbg("sync_account:qb_client", qb_info)
		print("[sync_account DBG] quickbooks_obj attributes:", qb_info)
	except Exception:
		pass

	# Get QuickBooks synced company once
	qb_company = _get_quickbooks_company(quickbooks_obj)
	if qb_company:
		_dbg("sync_account:qb_company", {"company": qb_company})
		# Ensure core root accounts and cost centers exist before syncing
		_ensure_root_accounts(qb_company)
		_ensure_root_cost_centers(qb_company)

	quickbooks_account_list = []

	# Base query without pagination
	account_query = (
		"SELECT Name, Active, Classification, AccountType, AccountSubType, "
		"CurrencyRef, Id, Description, AcctNum, ParentRef "
		"FROM Account order Id Desc"
	)

	# Use pagination helper to fetch all accounts
	all_accounts = query_with_pagination(
		quickbooks_obj, account_query, "Account", module_name="sync_account", data_name="accounts_list"
	)

	_dbg("sync_account:fetched_all", {"total_count": len(all_accounts)})

	# Save raw QuickBooks account data to JSON file for debugging
	try:
		save_qb_data_to_json(
			data=all_accounts,
			data_name="accounts_list",
			module_name="sync_account",
			full_response={"QueryResponse": {"Account": all_accounts}},
		)
	except Exception as e:
		_dbg("sync_account:debug_save_error", {"error": str(e)})
		# Don't fail the sync if debug file save fails

	# Initialize generic Import Tracker
	from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker

	tracker = ImportTracker(
		len(all_accounts), "qb_account_import.log", module_name="ACCOUNT", company=qb_company
	)

	stats = sync_qb_accounts(all_accounts, quickbooks_account_list, qb_company, tracker=tracker)
	_dbg(
		"sync_account:done",
		{"created": len(quickbooks_account_list), "total_accounts": len(all_accounts), "stats": stats},
	)

	# Log summary if there were failures
	if stats.get("failed", 0) > 0:
		failed_accounts = stats.get("failed_accounts", [])
		error_msg = _(
			"Account sync completed with {0} error(s). "
			"{1} accounts failed to sync. "
			"Please check the Activity Log for details."
		).format(stats["failed"], stats["failed"])

		if failed_accounts:
			failed_names = [acc.get("name", "Unknown") for acc in failed_accounts[:10]]
			error_msg += _("\n\nFailed accounts (first 10): {0}").format(", ".join(failed_names))

		qb_log_error(
			title=_("Account Sync Completed with Errors"),
			status="Error",
			method="sync_account",
			message=error_msg,
			module="sync_account",
			request_data={"failed_accounts": failed_accounts[:10]},
		)


def sync_qb_accounts(get_qb_account, quickbooks_account_list, qb_company=None, tracker=None):
	stats = {"total": len(get_qb_account), "created": 0, "skipped": 0, "failed": 0, "failed_accounts": []}

	# Resolve company once for all accounts
	resolved_company = (
		qb_company
		or frappe.defaults.get_user_default("company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {}, "name")
	)

	# Performance optimization: Batch commits every 20 accounts instead of each one
	commit_interval = 20
	last_commit_count = 0

	# Initialize generic Import Tracker if not provided
	if not tracker:
		from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker

		tracker = ImportTracker(
			len(get_qb_account), "qb_account_import.log", module_name="ACCOUNT", company=resolved_company
		)

	for idx, qb_account in enumerate(get_qb_account):
		account_id = qb_account.get("Id")
		account_name = qb_account.get("Name", "Unknown")

		# Check if account exists - use unique QB ID with company prefix
		from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

		unique_account_id = make_unique_qb_id(str(account_id), resolved_company)
		account_filters = {"quickbooks_account_id": unique_account_id}
		if resolved_company:
			account_filters["company"] = resolved_company

		# Extract account display name for logging
		account_display_name = (
			qb_account.get("Name")
			or qb_account.get("FullyQualifiedName")
			or qb_account.get("name")
			or "Unknown"
		)

		# Log start of processing for this account
		tracker_id = f"{unique_account_id} ({account_display_name})"
		tracker.log_processing("QuickBooks", tracker_id)

		exists = frappe.db.get_value("Account", account_filters, "name")
		if not exists:
			_dbg("sync_qb_accounts:create", {"idx": idx, "Id": account_id, "name": account_name})
			try:
				create_account(qb_account, quickbooks_account_list, qb_company)
				stats["created"] += 1
				tracker.log_success("QuickBooks", tracker_id, "CREATED")
			except Exception as e:
				# Track failed accounts
				stats["failed"] += 1
				tracker.log_error("QuickBooks", tracker_id, str(e))
				stats["failed_accounts"].append({"qb_id": account_id, "name": account_name, "error": str(e)})
				_dbg(
					"sync_qb_accounts:create_failed",
					{"idx": idx, "Id": account_id, "name": account_name, "error": str(e)},
				)
				# Log error for this specific account
				qb_log_error(
					title=_("Failed to Create Account"),
					status="Error",
					method="sync_qb_accounts",
					message=_("Failed to create account '{0}' (QuickBooks ID: {1}). " "Error: {2}").format(
						account_name, account_id, str(e)
					),
					module="sync_account",
					request_data=qb_account,
				)
		else:
			stats["skipped"] += 1
			tracker.log_skip("QuickBooks", tracker_id, "already_exists")
			_dbg(
				"sync_qb_accounts:skip_exists",
				{"idx": idx, "Id": account_id, "name": exists},
			)

		# Batch commit every N accounts for performance
		current_processed = stats["created"] + stats["failed"] + stats["skipped"]
		if current_processed > 0 and (current_processed - last_commit_count) >= commit_interval:
			frappe.db.commit()
			last_commit_count = current_processed

	# Final commit for any remaining accounts
	if stats["created"] > 0 or stats["failed"] > 0:
		frappe.db.commit()

	# Log final summary
	tracker.log_summary()

	# Post-sync: Ensure default accounts are set on the company
	try:
		resolved_company = (
			qb_company
			or frappe.defaults.get_user_default("company")
			or frappe.db.get_single_value("Global Defaults", "default_company")
			or frappe.db.get_value("Company", {}, "name")
		)
		if resolved_company:
			company_doc = frappe.get_doc("Company", resolved_company)
			ensure_all_defaults(company_doc)
			_dbg("sync_account:defaults_ensured", {"company": resolved_company})
	except Exception as e:
		_dbg("sync_account:defaults_failed", {"error": str(e)})
		# Don't fail the sync just because defaults setting failed, but log it.
		qb_log_error(title="Failed to Set Company Defaults", message=str(e), module="sync_account")

	return stats


def create_account(qb_account, quickbooks_account_list, qb_company=None):
	"""
	Create Account in ERPNext from QuickBooks account data.

	This function flattens the QuickBooks account hierarchy:
	- ALL accounts are created as child accounts (is_group = False)
	- All accounts are assigned under default company parent accounts based on root_type
	- Account details are preserved (name, number, description, currency, etc.)

	Args:
	    qb_account (dict): QuickBooks account data
	    quickbooks_account_list (list): List to track synced account IDs
	    qb_company (str, optional): ERPNext company name

	Returns:
	    list: Updated quickbooks_account_list
	"""
	# Resolve company
	Default_company = (
		qb_company
		or frappe.defaults.get_user_default("company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {}, "name")
	)

	if not Default_company:
		raise ValueError("No company found. Please set a default company.")

	Company_abbr = frappe.db.get_value("Company", {"name": Default_company}, "abbr")
	if not Company_abbr:
		raise ValueError(f"Company '{Default_company}' not found or has no abbreviation")

	_dbg("create_account:resolved_company", {"company": Default_company, "qb_company": qb_company})

	# Check if account already exists by QuickBooks ID
	from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

	unique_qb_id = make_unique_qb_id(str(qb_account.get("Id")), Default_company)
	account_filters = {"quickbooks_account_id": unique_qb_id, "company": Default_company}

	existing_by_qb_id = frappe.db.get_value("Account", account_filters, "name")
	if existing_by_qb_id:
		_dbg(
			"create_account:skip_exists_by_qb_id",
			{"qb_id": qb_account.get("Id"), "existing": existing_by_qb_id, "company": Default_company},
		)
		quickbooks_account_list.append(str(qb_account.get("Id")))
		return quickbooks_account_list

	# Build account name (with optional suffix from settings)
	qb_suffix = ""
	try:
		qb_settings = frappe.get_doc("Quickbooks Settings")
		qb_suffix = getattr(qb_settings, "account_name_suffix", "") or ""
	except Exception:
		pass

	base_name = str(qb_account.get("Name"))
	proposed_account_name = f"{base_name} - {qb_suffix}" if qb_suffix else base_name

	# Determine root_type and account_type using improved logic
	root_type, mapped_account_type = get_erpnext_root_and_account_type(qb_account)

	_dbg(
		"create_account:mapping_result",
		{
			"qb_type": qb_account.get("AccountType"),
			"qb_subtype": qb_account.get("AccountSubType"),
			"root_type": root_type,
			"account_type": mapped_account_type,
			"account_name": qb_account.get("Name"),
		},
	)

	# Get default parent account for this root_type
	try:
		parent_account = _get_default_parent_account(Default_company, root_type)
	except Exception as e:
		_dbg(
			"create_account:parent_error",
			{"error": str(e), "root_type": root_type, "account_name": qb_account.get("Name")},
		)
		raise

	# Simple check: if account with same name exists and has no QB ID, link it
	existing_by_name = frappe.db.get_value(
		"Account",
		{"account_name": proposed_account_name, "company": Default_company},
		["name", "quickbooks_account_id"],
		as_dict=True,
	)

	if existing_by_name and not existing_by_name.get("quickbooks_account_id"):
		# Existing account has no QB ID - link it
		try:
			frappe.db.set_value(
				"Account", existing_by_name.get("name"), "quickbooks_account_id", unique_qb_id
			)
			# Note: Commit will be batched in sync_qb_accounts() for better performance
			quickbooks_account_list.append(str(qb_account.get("Id")))
			_dbg(
				"create_account:linked_existing",
				{"account": existing_by_name.get("name"), "qb_id": qb_account.get("Id")},
			)
			return quickbooks_account_list
		except Exception:
			pass
	elif existing_by_name and existing_by_name.get("quickbooks_account_id") != unique_qb_id:
		# Account with same name exists but different QB ID - append suffix
		suffix = f" - {qb_account.get('Id')}"
		proposed_account_name = f"{proposed_account_name}{suffix}"
		_dbg(
			"create_account:renamed_duplicate",
			{
				"original_name": base_name,
				"new_name": proposed_account_name,
				"reason": "Duplicate name with different QB ID",
			},
		)

	try:
		# Create the account as a child account (is_group = False)
		account = frappe.new_doc("Account")
		account.company = Default_company
		account.quickbooks_account_id = unique_qb_id
		account.account_name = proposed_account_name
		account.is_group = False  # ALL accounts are child accounts
		account.parent_account = parent_account
		account.root_type = root_type

		# Map Account Type from improved logic
		if mapped_account_type:
			account.account_type = mapped_account_type

		# Map Account Number from QuickBooks AcctNum
		account_number = qb_account.get("AcctNum")
		if account_number:
			# Check if account number already exists for a different account
			existing_with_acct_num = frappe.db.get_value(
				"Account",
				{"account_number": str(account_number), "company": Default_company},
				["name", "quickbooks_account_id"],
				as_dict=True,
			)

			if existing_with_acct_num:
				existing_qb_id = existing_with_acct_num.get("quickbooks_account_id")
				# Only set account number if it's the same account or not in use
				if existing_qb_id == unique_qb_id:
					account.account_number = str(account_number)
				else:
					_dbg(
						"create_account:duplicate_account_number",
						{
							"account_number": account_number,
							"existing_account": existing_with_acct_num.get("name"),
							"note": "Account number already used by different account - not setting",
						},
					)
			else:
				account.account_number = str(account_number)

		# Map Description from QuickBooks Description
		if qb_account.get("Description"):
			account.description = str(qb_account.get("Description"))

		# Keep all accounts enabled
		account.disabled = 0

		# Map Currency
		currency_ref = qb_account.get("CurrencyRef")
		if currency_ref and currency_ref.get("value"):
			account.account_currency = currency_ref.get("value")

		account.flags.ignore_mandatory = True
		_dbg(
			"create_account:insert",
			{
				"account_name": account.account_name,
				"parent": parent_account,
				"root_type": root_type,
				"company": Default_company,
				"qb_id": unique_qb_id,
				"is_group": False,
			},
		)

		try:
			account.insert()
		except frappe.DuplicateEntryError:
			# Handle duplicate name by appending suffix
			suffix = f" ({qb_account.get('Id')})"
			account.account_name = f"{base_name}{suffix}"
			_dbg(
				"create_account:retry_duplicate_name",
				{
					"original_name": proposed_account_name,
					"new_name": account.account_name,
					"reason": "DuplicateEntryError on insert",
				},
			)
			account.insert()

		# Note: Commit will be batched in sync_qb_accounts() for better performance

		quickbooks_account_list.append(str(qb_account.get("Id")))
		_dbg(
			"create_account:success",
			{
				"account": account.name,
				"account_name": account.account_name,
				"qb_id": unique_qb_id,
				"company": Default_company,
			},
		)

	except Exception as e:
		first_arg = str(e.args[0]) if (hasattr(e, "args") and e.args) else ""
		_dbg(
			"create_account:error",
			{
				"first_arg": first_arg,
				"error": str(e),
				"account_name": qb_account.get("Name"),
				"account_id": qb_account.get("Id"),
			},
		)
		if first_arg.startswith("402"):
			# Re-raise authentication errors
			raise e
		else:
			# Log exception but re-raise so sync_qb_accounts can track it
			qb_log_exception(
				method="create_account",
				err=e,
				request_data=qb_account,
				module="sync_account",
			)
			# Re-raise so the caller can track failed accounts
			raise e

	return quickbooks_account_list


"""Sync ERPNext Account to QuickBooks"""


def sync_erp_accounts():
	"""Recive Response From Quickbooks and Update quickbooks_account_id in Account"""
	response_from_quickbooks = sync_erp_accounts_to_quickbooks()
	if response_from_quickbooks:
		try:
			from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id

			for response_obj in response_from_quickbooks.successes:
				if response_obj:
					# Get company from account to create unique QB ID
					account_company = frappe.db.get_value("Account", response_obj.Name, "company")
					if account_company:
						unique_qb_id = make_unique_qb_id(str(response_obj.Id), account_company)
						frappe.db.sql(
							"""UPDATE tabAccount SET quickbooks_account_id = %s WHERE name = %s""",
							(unique_qb_id, response_obj.Name),
						)
					else:
						# Fallback if company not found
						frappe.db.sql(
							"""UPDATE tabAccount SET quickbooks_account_id = %s WHERE name = %s""",
							(str(response_obj.Id), response_obj.Name),
						)
				else:
					raise _("Does not get any response from quickbooks")
		except Exception as e:
			qb_log_exception(
				method="sync_erp_accounts",
				err=e,
				request_data=response_obj,
				module="sync_account",
			)


def sync_erp_accounts_to_quickbooks():
	Account_list = []
	for erp_account in erp_account_data():
		try:
			if erp_account:
				create_erp_account_to_quickbooks(erp_account, Account_list)
			else:
				raise _("Account does not exist in ERPNext")
		except Exception as e:
			if e.args[0] and e.args[0].startswith("402"):
				raise e
			else:
				qb_log_exception(
					method="sync_erp_accounts_to_quickbooks",
					err=e,
					request_data=erp_account,
					module="sync_account",
				)
	results = batch_create(Account_list)
	return results


def erp_account_data():
	erp_account = frappe.db.sql(
		"""select name, root_type, account_type, quickbooks_account_id from `tabAccount` where is_group =0 && quickbooks_account_id is NULL""",
		as_dict=1,
	)
	return erp_account


def create_erp_account_to_quickbooks(erp_account, Account_list):
	account_obj = Account()
	account_obj.Name = erp_account.name
	account_obj.FullyQualifiedName = erp_account.name
	account_classification_and_account_type(account_obj, erp_account)
	account_obj.save()
	Account_list.append(account_obj)
	return Account_list


def account_classification_and_account_type(account_obj, erp_account):
	if erp_account.root_type == "Asset":
		account_obj.Classification = erp_account.root_type
		account_obj.AccountType = "Other Current Asset"
		account_obj.AccountSubType = "AllowanceForBadDebts"
	elif erp_account.root_type == "Liability":
		account_obj.Classification = erp_account.root_type
		account_obj.AccountType = "Liability"
		account_obj.AccountSubType = "OtherCurrentLiabilities"
	elif erp_account.root_type == "Expense":
		account_obj.Classification = erp_account.root_type
		account_obj.AccountType = "Other Expense"
		account_obj.AccountSubType = "Amortization"
	elif erp_account.root_type == "Income":
		account_obj.Classification = erp_account.root_type
		account_obj.AccountType = "Income"
		account_obj.AccountSubType = "SalesOfProductIncome"
	elif erp_account.root_type == "Equity":
		account_obj.Classification = erp_account.root_type
		account_obj.AccountType = "Equity"
		account_obj.AccountSubType = "RetainedEarnings"
	else:
		account_obj.Classification = None
		account_obj.AccountType = "Cost of Goods Sold"
