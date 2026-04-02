"""
Compare QuickBooks and ERPNext Account Balances

This module provides functionality to cross-check account balances between
QuickBooks and ERPNext to identify discrepancies.

Usage:
    from quickbooks_master_sync.quickbooks_master_sync.sync.compare_balances import compare_account_balances

    # Compare balances for a company
    result = compare_account_balances(company_name="ABC Company")
"""

from datetime import datetime

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from ..utils.logging import qb_log_error, qb_log_status
from .sync_utils import _dbg, _get_quickbooks_company, query_with_pagination, save_qb_data_to_json


@frappe.whitelist()
def compare_account_balances(company_name=None, as_of_date=None, tolerance=0.01):
	"""
	Compare account balances between QuickBooks and ERPNext.

	Args:
	    company_name (str): Name of the company to compare balances for
	    as_of_date (str): Date to compare balances as of (default: today)
	    tolerance (float): Tolerance for balance differences (default: 0.01)

	Returns:
	    dict: Comparison results with matched, mismatched, and missing accounts
	"""
	if not company_name:
		company_name = frappe.defaults.get_user_default("company")
		if not company_name:
			frappe.throw(_("Please specify a company"))

	if not as_of_date:
		as_of_date = today()
	else:
		as_of_date = getdate(as_of_date)

	# Get QuickBooks connection
	try:
		from frappe.utils import cint

		from quickbooks_master_sync.pyqb.quickbooks import QuickBooks

		qs = frappe.get_doc("Quickbooks Settings")
		if not qs.enable_quickbooks_online:
			frappe.throw(_("QuickBooks Online is not enabled. Please enable it in QuickBooks Settings."))

		# Use helper function which intelligently handles singleton clearing
		from ..api import _create_quickbooks_client

		quickbooks_obj = _create_quickbooks_client(qs)
	except Exception as e:
		frappe.throw(_("Failed to connect to QuickBooks: {0}").format(str(e)))

	# _dbg("compare_balances:start", {
	#     "company": company_name,
	#     "as_of_date": str(as_of_date),
	#     "tolerance": tolerance
	# })

	# Fetch QuickBooks account balances
	qb_balances = _fetch_quickbooks_balances(quickbooks_obj, company_name, as_of_date)

	# Fetch ERPNext account balances
	erp_balances = _fetch_erpnext_balances(company_name, as_of_date)

	# Compare balances
	comparison = _compare_balances(qb_balances, erp_balances, tolerance)

	# Log results
	_log_comparison_results(comparison, company_name, as_of_date)

	return comparison


def _fetch_quickbooks_balances(quickbooks_obj, company_name, as_of_date):
	"""Fetch account balances from QuickBooks using Trial Balance Report"""
	# _dbg("compare_balances:fetch_qb", {"company": company_name, "as_of_date": str(as_of_date)})

	try:
		# Construct URL for Trial Balance Report
		# /company/:id/reports/TrialBalance?end_date=YYYY-MM-DD&date_macro=Custom
		report_url = quickbooks_obj.api_url + f"/company/{quickbooks_obj.company_id}/reports/TrialBalance"

		params = {"date_macro": "All"}

		# Helper to construct URL with params since make_request doesn't support arbitrary params
		from urllib.parse import urlencode

		query_string = urlencode(params)
		full_url = f"{report_url}?{query_string}"

		# Call GET request
		response = quickbooks_obj.make_request("GET", full_url, {})

		# Parse Response
		# Structure: Rows -> Row (list) -> ColData
		rows = response.get("Rows", {}).get("Row", [])
		if not rows:
			return {}

		balances = {}
		currency = response.get("Header", {}).get("Currency", "USD")

		# Helper function to parse rows recursively (handling sections if any)
		def parse_rows(row_list):
			for row in row_list:
				# Check for nested rows (Section)
				if "Rows" in row:
					parse_rows(row["Rows"].get("Row", []))
					continue

				# Check for "ColData" - this is a data row
				if "ColData" in row:
					col_data = row["ColData"]

					# Columns:
					# 0: Account (value=Name, id=ID)
					# 1: Debit
					# 2: Credit

					if len(col_data) < 2:
						continue

					acc_col = col_data[0]
					# Check if it's a data row (has ID)
					if not isinstance(acc_col, dict) or "id" not in acc_col:
						continue

					account_id = str(acc_col.get("id"))
					account_name = acc_col.get("value")

					if not account_id:
						continue

					# Extract debit and credit
					debit_val = 0.0
					credit_val = 0.0

					if len(col_data) > 1:
						debit_val = flt(col_data[1].get("value", 0))

					if len(col_data) > 2:
						credit_val = flt(col_data[2].get("value", 0))

					# Balance = Debit - Credit (Signed)
					balance = debit_val - credit_val

					balances[account_id] = {
						"qb_id": account_id,
						"qb_name": account_name,
						"qb_balance": balance,
						"currency": currency,
						"account_type": "",  # Not available in simple report
						"classification": "",  # Not available
						"active": True,  # Assume active if in report
						"raw_balance": balance,
					}

		parse_rows(rows)
		return balances

	except Exception as e:
		_dbg("compare_balances:qb_fetch_error", {"error": str(e)})
		frappe.throw(_("Failed to fetch QuickBooks balances: {0}").format(str(e)))


def _fetch_erpnext_balances(company_name, as_of_date):
	"""Fetch account balances from ERPNext"""
	# _dbg("compare_balances:fetch_erp", {
	#     "company": company_name,
	#     "as_of_date": str(as_of_date)
	# })

	# Get all accounts with QuickBooks IDs for this company
	accounts = frappe.get_all(
		"Account",
		filters={
			"company": company_name,
			"is_group": 0,  # Only leaf accounts
		},
		fields=[
			"name",
			"account_name",
			"quickbooks_account_id",
			"account_type",
			"root_type",
			"account_currency",
		],
	)

	balances = {}

	for acc in accounts:
		stored_qb_id = str(acc.get("quickbooks_account_id", ""))
		if not stored_qb_id:
			continue

		# Parse the raw QB ID from the stored unique ID (which may have company prefix)
		from ..utils.qb_id_utils import parse_qb_id

		_company_abbr, qb_id = parse_qb_id(stored_qb_id)  # Extract raw ID for comparison with QuickBooks
		if not qb_id:
			_dbg(
				"compare_balances:parse_failed",
				{"account": acc["name"], "stored_qb_id": stored_qb_id, "company": company_name},
			)
			continue  # Skip if parsing failed

		# Calculate balance from GL Entry up to as_of_date
		balance = _calculate_erpnext_balance(acc["name"], company_name, as_of_date)

		balances[qb_id] = {
			"erp_account": acc["name"],
			"erp_name": acc.get("account_name", ""),
			"account_type": acc.get("account_type", ""),
			"root_type": acc.get("root_type", ""),
			"erp_balance": balance,
			"currency": acc.get("account_currency")
			or frappe.get_cached_value("Company", company_name, "default_currency"),
			"stored_qb_id": stored_qb_id,  # Keep original for debugging
			"parsed_qb_id": qb_id,  # Parsed raw ID
		}

	_dbg(
		"compare_balances:erp_fetched",
		{
			"total_accounts": len(balances),
			"company": company_name,
			"accounts_with_balance": len([b for b in balances.values() if b["erp_balance"] != 0]),
			"sample_accounts": list(balances.keys())[:5] if balances else [],
		},
	)

	return balances


def _calculate_erpnext_balance(account, company, as_of_date):
	"""Calculate account balance from GL Entry"""
	try:
		# Get balance from GL Entry
		# For asset/expense: debit - credit
		# For liability/equity/income: credit - debit
		frappe.get_doc("Account", account)

		# Query GL Entry for balance - only include submitted entries (exclude cancelled)
		gl_entries = frappe.db.sql(
			"""
            SELECT
                SUM(CASE WHEN debit > 0 THEN debit ELSE 0 END) as total_debit,
                SUM(CASE WHEN credit > 0 THEN credit ELSE 0 END) as total_credit,
                COUNT(*) as entry_count
            FROM `tabGL Entry`
            WHERE account = %s
            AND company = %s
            AND posting_date <= %s
            AND is_cancelled = 0
            AND docstatus = 1
        """,
			(account, company, as_of_date),
			as_dict=True,
		)

		if not gl_entries or not gl_entries[0]:
			return 0.0

		total_debit = flt(gl_entries[0].get("total_debit", 0) or 0)
		total_credit = flt(gl_entries[0].get("total_credit", 0) or 0)
		gl_entries[0].get("entry_count", 0)

		# Calculate balance as Debit - Credit (Signed)
		balance = total_debit - total_credit

		# Log large balances for investigation
		# if abs(balance) > 100000:
		# _dbg("compare_balances:large_balance", {
		#     "account": account,
		#     "root_type": root_type,
		#     "total_debit": total_debit,
		#     "total_credit": total_credit,
		#     "balance": balance,
		#     "entry_count": entry_count
		# })

		return balance

	except Exception as e:
		_dbg("compare_balances:calc_balance_error", {"account": account, "error": str(e)})
		return 0.0


def _compare_balances(qb_balances, erp_balances, tolerance):
	"""Compare QuickBooks and ERPNext balances"""
	_dbg(
		"compare_balances:compare_start",
		{
			"qb_count": len(qb_balances),
			"erp_count": len(erp_balances),
			"qb_ids_sample": list(qb_balances.keys())[:5] if qb_balances else [],
			"erp_ids_sample": list(erp_balances.keys())[:5] if erp_balances else [],
		},
	)

	matched = []
	mismatched = []
	qb_only = []
	erp_only = []

	# Check accounts in QuickBooks
	for qb_id, qb_data in qb_balances.items():
		if qb_id in erp_balances:
			erp_data = erp_balances[qb_id]
			qb_balance = qb_data["qb_balance"]
			erp_balance = erp_data["erp_balance"]

			# Log significant differences for debugging (especially sign mismatches)
			# if abs(qb_balance - erp_balance) > 1000 or (qb_balance * erp_balance < 0 and abs(qb_balance) > 100):
			#     _dbg("compare_balances:large_difference", {
			#         "qb_id": qb_id,
			#         "qb_name": qb_data["qb_name"],
			#         "qb_balance": qb_balance,
			#         "erp_balance": erp_balance,
			#         "difference": abs(qb_balance - erp_balance),
			#         "qb_classification": qb_data.get("classification"),
			#         "qb_account_type": qb_data.get("account_type"),
			#         "erp_root_type": erp_data.get("root_type"),
			#         "sign_mismatch": qb_balance * erp_balance < 0
			#     })

			difference = abs(qb_balance - erp_balance)

			if difference <= tolerance:
				matched.append(
					{
						"qb_id": qb_id,
						"qb_name": qb_data["qb_name"],
						"erp_account": erp_data["erp_account"],
						"erp_name": erp_data["erp_name"],
						"qb_balance": qb_balance,
						"erp_balance": erp_balance,
						"difference": difference,
						"account_type": qb_data["account_type"],
						"currency": qb_data.get("currency", "USD"),
					}
				)
			else:
				mismatched.append(
					{
						"qb_id": qb_id,
						"qb_name": qb_data["qb_name"],
						"erp_account": erp_data["erp_account"],
						"erp_name": erp_data["erp_name"],
						"qb_balance": qb_balance,
						"erp_balance": erp_balance,
						"difference": difference,
						"account_type": qb_data["account_type"],
						"currency": qb_data.get("currency", "USD"),
					}
				)
		else:
			# Account exists in QuickBooks but not in ERPNext
			# Include it in qb_only if it has a balance OR if it's a parent account (might need to be synced)
			qb_balance = qb_data["qb_balance"]
			has_subaccounts = qb_data.get("has_subaccounts", False)

			# Include if:
			# 1. Has a balance (non-zero), OR
			# 2. Is a parent account (even with zero balance, it might need to be synced if it has subaccounts)
			if abs(qb_balance) > 0.001 or (
				has_subaccounts and abs(qb_data.get("qb_balance_with_subs", 0)) > 0.001
			):
				qb_only.append(
					{
						"qb_id": qb_id,
						"qb_name": qb_data["qb_name"],
						"qb_balance": qb_balance,
						"qb_balance_with_subs": qb_data.get("qb_balance_with_subs", 0),
						"account_type": qb_data["account_type"],
						"currency": qb_data.get("currency", "USD"),
						"is_parent_account": has_subaccounts,
						"note": "Parent account with balance - may need to be synced to ERPNext"
						if has_subaccounts
						else None,
					}
				)

	# Check accounts in ERPNext but not in QuickBooks
	for qb_id, erp_data in erp_balances.items():
		if qb_id not in qb_balances:
			if erp_data["erp_balance"] != 0:  # Only report if has balance
				erp_only.append(
					{
						"qb_id": qb_id,
						"erp_account": erp_data["erp_account"],
						"erp_name": erp_data["erp_name"],
						"erp_balance": erp_data["erp_balance"],
						"account_type": erp_data["account_type"],
						"currency": erp_data.get("currency", "USD"),
					}
				)

	# Calculate summary
	# Note: Summing all balances may not be meaningful due to different account types
	# But we'll calculate it for comparison purposes
	total_qb_balance = sum(b["qb_balance"] for b in qb_balances.values())
	total_erp_balance = sum(b["erp_balance"] for b in erp_balances.values())
	total_difference = total_qb_balance - total_erp_balance

	# Calculate Debit vs Credit totals
	# Debit balances are positive, Credit balances are negative (in our written logic)
	total_qb_debit = sum(b["qb_balance"] for b in qb_balances.values() if b["qb_balance"] > 0)
	total_qb_credit = sum(b["qb_balance"] for b in qb_balances.values() if b["qb_balance"] < 0)

	total_erp_debit = sum(b["erp_balance"] for b in erp_balances.values() if b["erp_balance"] > 0)
	total_erp_credit = sum(b["erp_balance"] for b in erp_balances.values() if b["erp_balance"] < 0)

	# Difference in Debits
	total_debit_difference = total_qb_debit - total_erp_debit

	# Difference in Credits
	total_credit_difference = total_qb_credit - total_erp_credit

	# Categorized Totals
	categories = ["Asset", "Liability", "Equity", "Income", "Expense"]
	cat_totals = {cat: {"qb": 0.0, "erp": 0.0} for cat in categories}
	cat_totals["Uncategorized"] = {"qb": 0.0, "erp": 0.0}

	def add_to_category(root_type, qb_val, erp_val):
		# Normalize root_type
		if not root_type:
			target = "Uncategorized"
		elif root_type in categories:
			target = root_type
		else:
			# Handle approximate matching or fallback
			if "Asset" in root_type:
				target = "Asset"
			elif "Liability" in root_type:
				target = "Liability"
			elif "Equity" in root_type:
				target = "Equity"
			elif "Income" in root_type:
				target = "Income"
			elif "Expense" in root_type:
				target = "Expense"
			else:
				target = "Uncategorized"

		cat_totals[target]["qb"] += qb_val
		cat_totals[target]["erp"] += erp_val

	# Aggregate Matched
	for m in matched:
		# For matched, use ERPNext root type (more reliable/available)
		# We need to look up root type. It's not in `m` dict primarily?
		# Check `matched.append`... `account_type` is there (from QB), `erp_data` had `root_type`.
		# Wait, `m` only has `account_type` from QB. `erp_data` logic had `root_type`.
		# I need to verify what is stored in `matched` list.
		# Lines 328-338: "account_type": qb_data["account_type"]
		# It DOES NOT store erp root type.
		# I need to fetch it or rely on `erp_balances` lookup.
		# Efficient way: `erp_balances[m['qb_id']]['root_type']`
		qb_id = m.get("qb_id")
		if qb_id and qb_id in erp_balances:
			rtype = erp_balances[qb_id].get("root_type")
			add_to_category(rtype, m["qb_balance"], m["erp_balance"])

	# Aggregate Mismatched
	for m in mismatched:
		qb_id = m.get("qb_id")
		if qb_id and qb_id in erp_balances:
			rtype = erp_balances[qb_id].get("root_type")
			add_to_category(rtype, m["qb_balance"], m["erp_balance"])

	# Aggregate ERP Only
	for e in erp_only:
		# e has `account_type`?... `erp_only.append` takes `erp_data` fields.
		# Need to check `erp_balances` structure.
		# `erp_balances` items have `root_type`.
		# `erp_only` items (lines 376-383) DO NOT store `root_type`.
		# I need to look it up using `qb_id` (which is key in `erp_balances`).
		qb_id = e.get("qb_id")
		# But wait, `erp_only` list is created from `erp_balances` iteration where `qb_id` is local loop var.
		# So I can re-fetch from `erp_balances` or better: modify the loops above to include `root_type` in the lists?
		# Modifying the loops above is cleaner but more code change.
		# Looking up `erp_balances[e['qb_id']]` is safe since `e` comes from it.
		if qb_id and qb_id in erp_balances:
			rtype = erp_balances[qb_id].get("root_type")
			add_to_category(rtype, 0.0, e["erp_balance"])

	# Aggregate QB Only
	for q in qb_only:
		# QB Only accounts don't have ERP mapping, so no ERP root type.
		# We might have `account_type` from QB if we parsed it (we didn't yet).
		# So they go to Uncategorized.
		add_to_category(None, q["qb_balance"], 0.0)

	# Flatten summary for response
	summary_data = {
		"total_qb_accounts": len(qb_balances),
		"total_erp_accounts": len(erp_balances),
		"matched_accounts": len(matched),
		"mismatched_accounts": len(mismatched),
		"qb_only_accounts": len(qb_only),
		"erp_only_accounts": len(erp_only),
		"total_qb_balance": total_qb_balance,
		"total_erp_balance": total_erp_balance,
		"total_difference": total_difference,
		"total_qb_debit": total_qb_debit,
		"total_qb_credit": total_qb_credit,
		"total_erp_debit": total_erp_debit,
		"total_erp_credit": total_erp_credit,
		"total_debit_difference": total_debit_difference,
		"total_credit_difference": total_credit_difference,
		"tolerance": tolerance,
	}

	# Add Categorized Totals
	for cat, totals in cat_totals.items():
		slug = cat.lower()
		summary_data[f"total_{slug}_qb"] = totals["qb"]
		summary_data[f"total_{slug}_erp"] = totals["erp"]
		summary_data[f"total_{slug}_diff"] = totals["qb"] - totals["erp"]

	result = {
		"summary": summary_data,
		"matched": matched,
		"mismatched": mismatched,
		"qb_only": qb_only,
		"erp_only": erp_only,
	}

	# _dbg("compare_balances:compare_done", {
	#     "matched": len(matched),
	#     "mismatched": len(mismatched),
	#     "qb_only": len(qb_only),
	#     "erp_only": len(erp_only)
	# })

	return result


def _log_comparison_results(comparison, company_name, as_of_date):
	"""Log comparison results"""
	summary = comparison["summary"]

	if (
		summary["mismatched_accounts"] > 0
		or summary["qb_only_accounts"] > 0
		or summary["erp_only_accounts"] > 0
	):
		# Has discrepancies
		message = _(
			"Balance Comparison Complete with Discrepancies\n"
			"Company: {0}\n"
			"As of Date: {1}\n"
			"Matched: {2}, Mismatched: {3}\n"
			"QuickBooks Only: {4}, ERPNext Only: {5}\n"
			"Total Difference: {6}"
		).format(
			company_name,
			as_of_date,
			summary["matched_accounts"],
			summary["mismatched_accounts"],
			summary["qb_only_accounts"],
			summary["erp_only_accounts"],
			summary["total_difference"],
		)

		qb_log_error(
			title=_("Balance Comparison - Discrepancies Found"),
			status="Error",
			method="compare_account_balances",
			message=message,
			module="compare_balances",
			request_data=summary,
		)
	else:
		# All matched
		message = _(
			"Balance Comparison Complete - All Balances Match\n"
			"Company: {0}\n"
			"As of Date: {1}\n"
			"Matched Accounts: {2}"
		).format(company_name, as_of_date, summary["matched_accounts"])

		qb_log_status(
			title=_("Balance Comparison - All Matched"),
			status="Success",
			method="compare_account_balances",
			message=message,
			module="compare_balances",
			request_data=summary,
		)


@frappe.whitelist()
def compare_date_wise_balances(
	company_name=None, start_date=None, end_date=None, erp_account=None, qb_id=None, tolerance=0.01
):
	"""
	Compare total daily debit and credit amounts between QuickBooks and ERPNext for a specific account.
	"""
	if not company_name:
		company_name = frappe.defaults.get_user_default("company")
		if not company_name:
			frappe.throw(_("Please specify a company"))

	if not start_date:
		start_date = "2015-01-01"

	start_date = getdate(start_date)

	if not end_date:
		end_date = today()
	else:
		end_date = getdate(end_date)

	# Get QuickBooks connection
	try:
		from ..api import _create_quickbooks_client

		qs = frappe.get_doc("Quickbooks Settings")
		quickbooks_obj = _create_quickbooks_client(qs)
	except Exception as e:
		frappe.throw(_("Failed to connect to QuickBooks: {0}").format(str(e)))

	# Get account root type for correct sign interpretation
	root_type = None
	if erp_account:
		root_type = frappe.get_cached_value("Account", erp_account, "root_type")

	# Fetch daily totals from both systems
	erp_totals = _fetch_erpnext_daily_totals(company_name, start_date, end_date, erp_account)
	qb_totals = _fetch_quickbooks_daily_totals(
		quickbooks_obj, start_date, end_date, qb_id, root_type=root_type
	)

	# Compare them
	comparison = _compare_date_wise(erp_totals, qb_totals, tolerance)

	return comparison


def _fetch_erpnext_daily_totals(company, start_date, end_date, erp_account=None):
	"""Fetch daily sum of debits and credits from ERPNext GL Entry"""
	inner_where = ""
	params = [company, start_date, end_date]

	if erp_account:
		inner_where = "AND account = %s"
		params.append(erp_account)

	raw_data = frappe.db.sql(
		f"""
        SELECT
            posting_date,
            SUM(debit) as total_debit,
            SUM(credit) as total_credit
        FROM `tabGL Entry`
        WHERE company = %s
        AND posting_date >= %s
        AND posting_date <= %s
        {inner_where}
        AND is_cancelled = 0
        AND docstatus = 1
        GROUP BY posting_date
        ORDER BY posting_date
    """,
		tuple(params),
		as_dict=True,
	)

	totals = {}
	for row in raw_data:
		date_str = str(row["posting_date"])
		totals[date_str] = {"erp_debit": flt(row["total_debit"]), "erp_credit": flt(row["total_credit"])}
	return totals


def _fetch_quickbooks_daily_totals(quickbooks_obj, start_date, end_date, qb_account_id=None, root_type=None):
	"""Fetch daily sum of debits and credits from QuickBooks General Ledger Report"""
	from frappe.utils import getdate

	try:
		report_url = quickbooks_obj.api_url + f"/company/{quickbooks_obj.company_id}/reports/GeneralLedger"

		params = {
			"start_date": str(start_date),
			"end_date": str(end_date),
		}

		if qb_account_id:
			params["account"] = qb_account_id

		from urllib.parse import urlencode

		query_string = urlencode(params)
		full_url = f"{report_url}?{query_string}"

		response = quickbooks_obj.make_request("GET", full_url, {})

		# Parse Rows
		rows = response.get("Rows", {}).get("Row", [])
		daily_totals = {}

		# Identify column indices from Columns metadata
		col_indices = {"date": -1, "debit": -1, "credit": -1, "amount": -1}
		columns_meta = response.get("Columns", {}).get("Column", [])

		for i, col in enumerate(columns_meta):
			col_type = col.get("ColType", "").lower()
			label = col.get("ColTitle", "").lower()
			col_key = ""
			for meta in col.get("MetaData", []):
				if meta.get("Name") == "ColKey":
					col_key = meta.get("Value", "").lower()

			if col_type == "date" or "date" in label or col_key == "tx_date":
				if col_indices["date"] == -1:
					col_indices["date"] = i
			elif col_type == "debit" or "debit" in label:
				col_indices["debit"] = i
			elif col_type == "credit" or "credit" in label:
				col_indices["credit"] = i
			elif "amount" in label or col_key == "subt_nat_amount":
				if col_indices["amount"] == -1:
					col_indices["amount"] = i

		def parse_report_rows(row_list, current_account_id=None):
			for row in row_list:
				# Sections often contain nested rows and represent specific accounts
				if row.get("type") == "Section":
					header = row.get("Header", {})
					col_data = header.get("ColData", [])
					new_account_id = current_account_id
					if col_data and isinstance(col_data[0], dict) and col_data[0].get("id"):
						new_account_id = str(col_data[0].get("id"))

					if "Rows" in row:
						parse_report_rows(row["Rows"].get("Row", []), new_account_id)
					continue

				# STRICT FILTER: Only process individual transaction rows (type='Data')
				if row.get("type") != "Data":
					continue

				# ALIGNMENT FIX: Only include rows for the requested account
				# This prevents over-counting if the report includes sub-accounts or multiple accounts
				if qb_account_id and current_account_id != str(qb_account_id):
					continue

				if "ColData" in row:
					col_data = row["ColData"]

					date_val = None
					debit_val = 0.0
					credit_val = 0.0

					# Robust check for date
					if col_indices["date"] != -1 and len(col_data) > col_indices["date"]:
						raw_date = col_data[col_indices["date"]].get("value")
						if raw_date:
							try:
								# Ensure it's a valid date string (e.g. YYYY-MM-DD)
								# This avoids picking up headings like "Total for 1010-Cash"
								# or summary rows that might have "Balance" or non-date text
								if raw_date and "Balance" not in raw_date:
									parsed_date = getdate(raw_date)
									if parsed_date:
										date_val = str(parsed_date)
							except Exception:
								# Not a valid date, skip this row
								pass

					if date_val:
						# Get Debit/Credit
						if col_indices["debit"] != -1 and len(col_data) > col_indices["debit"]:
							debit_val = flt(col_data[col_indices["debit"]].get("value", 0))
						if col_indices["credit"] != -1 and len(col_data) > col_indices["credit"]:
							credit_val = flt(col_data[col_indices["credit"]].get("value", 0))

						# Handle single "Amount" column
						if (
							debit_val == 0
							and credit_val == 0
							and col_indices["amount"] != -1
							and len(col_data) > col_indices["amount"]
						):
							amt = flt(col_data[col_indices["amount"]].get("value", 0))

							# QuickBooks signed amounts in GL report are usually "Net Change"
							# where positive means Normal Balance increases.
							# For Liability/Equity/Income, normal balance is Credit.
							# For Asset/Expense, normal balance is Debit.
							is_normal_credit = root_type in ["Liability", "Equity", "Income"]

							if is_normal_credit:
								if amt > 0:
									credit_val = amt
								else:
									debit_val = abs(amt)
							else:
								if amt > 0:
									debit_val = amt
								else:
									credit_val = abs(amt)

						if date_val not in daily_totals:
							daily_totals[date_val] = {"qb_debit": 0.0, "qb_credit": 0.0}
						daily_totals[date_val]["qb_debit"] += debit_val
						daily_totals[date_val]["qb_credit"] += credit_val
					else:
						# Log rows skipped due to missing/invalid date for debugging
						# but only if they have some amount to avoid noise
						has_amount = False
						if col_indices["amount"] != -1 and len(col_data) > col_indices["amount"]:
							has_amount = flt(col_data[col_indices["amount"]].get("value", 0)) != 0

						if has_amount:
							_dbg(
								"compare_balances:skipped_row",
								{"row_type": row.get("type"), "col_data": col_data},
							)

		parse_report_rows(rows)

		# Final debug log of identified indices and results count
		_dbg("compare_balances:indices", col_indices)
		_dbg("compare_balances:qb_totals_count", len(daily_totals))

		return daily_totals

	except Exception as e:
		_dbg("compare_balances:qb_gl_fetch_error", {"error": str(e)})
		return {}


def _compare_date_wise(erp_totals, qb_totals, tolerance=0.01):
	"""Merge and compare daily totals"""
	all_dates = sorted(list(set(erp_totals.keys()) | set(qb_totals.keys())), reverse=True)

	comparison_results = []

	for date_str in all_dates:
		erp = erp_totals.get(date_str, {"erp_debit": 0.0, "erp_credit": 0.0})
		qb = qb_totals.get(date_str, {"qb_debit": 0.0, "qb_credit": 0.0})

		debit_diff = abs(erp["erp_debit"] - qb["qb_debit"])
		credit_diff = abs(erp["erp_credit"] - qb["qb_credit"])

		is_matched = debit_diff <= tolerance and credit_diff <= tolerance

		comparison_results.append(
			{
				"date": date_str,
				"erp_debit": erp["erp_debit"],
				"erp_credit": erp["erp_credit"],
				"qb_debit": qb["qb_debit"],
				"qb_credit": qb["qb_credit"],
				"debit_diff": debit_diff,
				"credit_diff": credit_diff,
				"matched": is_matched,
			}
		)

	return comparison_results


@frappe.whitelist()
def compare_party_balances(party_type, company_name=None, as_of_date=None, tolerance=0.01):
	"""
	Compare balances for Customers or Suppliers between QuickBooks and ERPNext.
	"""
	if not company_name:
		company_name = frappe.defaults.get_user_default("company")

	if not as_of_date:
		as_of_date = today()

	# Get QuickBooks connection
	try:
		from .api import _create_quickbooks_client

		qs = frappe.get_doc("Quickbooks Settings")
		quickbooks_obj = _create_quickbooks_client(qs)
	except Exception as e:
		frappe.throw(_("Failed to connect to QuickBooks: {0}").format(str(e)))

	# Fetch Balances
	erp_balances = _fetch_erpnext_party_balances(company_name, party_type, as_of_date)
	qb_balances = _fetch_quickbooks_party_balances(quickbooks_obj, party_type, as_of_date)

	# Compare
	comparison = _compare_party_balances(qb_balances, erp_balances, flt(tolerance), party_type)

	return comparison


def _fetch_erpnext_party_balances(company, party_type, as_of_date):
	"""Fetch net balances for all parties from GL Entry"""
	party_id_field = "quickbooks_cust_id" if party_type == "Customer" else "quickbooks_supp_id"

	# Get all parties with QB IDs
	parties = frappe.get_all(party_type, filters={"custom_company": company}, fields=["name", party_id_field])

	party_map = {p.name: p.get(party_id_field) for p in parties if p.get(party_id_field)}
	if not party_map:
		return {}

	# Query GL entries for these parties
	gl_data = frappe.db.sql(
		"""
        SELECT
            party,
            SUM(debit) - SUM(credit) as net_balance
        FROM `tabGL Entry`
        WHERE company = %s
        AND party_type = %s
        AND party IN %s
        AND posting_date <= %s
        AND is_cancelled = 0
        GROUP BY party
    """,
		(company, party_type, list(party_map.keys()), as_of_date),
		as_dict=True,
	)

	balances = {}
	for row in gl_data:
		qb_id_full = party_map.get(row.party)
		# Unique QB ID format is typically company_prefix-resource-id
		# We need the naked ID to match with QB report which only returns numeric ID
		qb_id = qb_id_full.split("-")[-1] if "-" in qb_id_full else qb_id_full

		balances[qb_id] = {"erp_name": row.party, "erp_balance": flt(row.net_balance), "qb_id": qb_id}

	return balances


def _fetch_quickbooks_party_balances(quickbooks_obj, party_type, as_of_date):
	"""Fetch party balances from QuickBooks using reports"""
	report_name = "CustomerBalance" if party_type == "Customer" else "VendorBalance"
	report_url = quickbooks_obj.api_url + f"/company/{quickbooks_obj.company_id}/reports/{report_name}"

	params = {"date_macro": "all"}

	from urllib.parse import urlencode

	full_url = f"{report_url}?{urlencode(params)}"

	try:
		response = quickbooks_obj.make_request("GET", full_url, {})
		rows = response.get("Rows", {}).get("Row", [])
		if not rows:
			return {}

		balances = {}

		def parse_report_rows(row_list):
			for row in row_list:
				if "Rows" in row:
					parse_report_rows(row["Rows"].get("Row", []))
					continue

				if "ColData" in row:
					col_data = row["ColData"]
					if len(col_data) < 2:
						continue

					party_col = col_data[0]
					# Data rows have 'id'
					if not isinstance(party_col, dict) or "id" not in party_col:
						continue

					party_id = str(party_col.get("id"))
					party_name = party_col.get("value")
					balance = flt(col_data[1].get("value", 0)) if len(col_data) > 1 else 0.0

					# For vendors/customers, report might return net balance
					# Net balance in QB Reports for CustomerBalance is typically positive for receivable
					# Net balance in QB Reports for VendorBalance is typically positive for payable?
					# Let's double check alignment with ERPNext later.

					balances[party_id] = {"qb_id": party_id, "qb_name": party_name, "qb_balance": balance}

		parse_report_rows(rows)
		return balances

	except Exception as e:
		_dbg("compare_party_balances:qb_fetch_error", {"error": str(e)})
		return {}


def _compare_party_balances(qb_balances, erp_balances, tolerance, party_type):
	"""Merge and compare party balances"""
	all_ids = set(qb_balances.keys()) | set(erp_balances.keys())
	results = []

	for qb_id in all_ids:
		qb = qb_balances.get(qb_id, {})
		erp = erp_balances.get(qb_id, {})

		qb_bal = qb.get("qb_balance", 0.0)
		erp_bal = erp.get("erp_balance", 0.0)

		# ERPNext: Debit is positive (+), Credit is negative (-).
		# Customer balance is typically Debit (+) -> Receivable (Matches QB Report positive)
		# Supplier balance is typically Credit (-) -> Payable
		# QuickBooks Reports:
		# VendorBalance usually returns POSITIVE for Payable.
		# CustomerBalance usually returns POSITIVE for Receivable.

		# Align signs for Supplier:
		# If Supplier, flip ERP balance so both systems show positive for payables.
		if party_type == "Supplier":
			erp_bal = -erp_bal

		diff = abs(erp_bal - qb_bal)
		is_matched = diff <= tolerance

		results.append(
			{
				"qb_id": qb_id,
				"qb_name": qb.get("qb_name", ""),
				"erp_name": erp.get("erp_name", ""),
				"erp_balance": erp_bal,
				"qb_balance": qb_bal,
				"difference": diff,
				"matched": is_matched,
			}
		)

	return results


@frappe.whitelist()
def get_daily_transactions_comparison(company_name, erp_account, qb_id, date):
	"""Fetch daily transactions for side-by-side comparison in a dialog."""
	if not company_name:
		company_name = frappe.defaults.get_user_default("company")

	# Get QuickBooks connection
	try:
		from .api import _create_quickbooks_client

		qs = frappe.get_doc("Quickbooks Settings")
		quickbooks_obj = _create_quickbooks_client(qs)
	except Exception as e:
		frappe.throw(_("Failed to connect to QuickBooks: {0}").format(str(e)))

	erp_txns = _fetch_erpnext_daily_transactions(company_name, erp_account, date)
	qb_txns = _fetch_quickbooks_daily_transactions(quickbooks_obj, qb_id, date)

	# Get account root type for frontend interpretation
	root_type = frappe.get_cached_value("Account", erp_account, "root_type")

	return {"erp_transactions": erp_txns, "qb_transactions": qb_txns, "root_type": root_type}


def _fetch_erpnext_daily_transactions(company, account, date):
	"""Fetch GL entries for a specific account on a specific date, aggregated by voucher."""
	gl_entries = frappe.db.get_all(
		"GL Entry",
		filters={"company": company, "account": account, "posting_date": date, "is_cancelled": 0},
		fields=[
			"voucher_type",
			"voucher_no",
			"sum(debit) as debit",
			"sum(credit) as credit",
			"MAX(remarks) as remarks",
			"party",
		],
		group_by="voucher_type, voucher_no, party",
		order_by="voucher_no asc",
	)
	return gl_entries


def _fetch_quickbooks_daily_transactions(quickbooks_obj, qb_id, date):
	"""Fetch transactions for a specific QB account from the General Ledger report."""
	report_url = quickbooks_obj.api_url + f"/company/{quickbooks_obj.company_id}/reports/GeneralLedger"

	params = {"start_date": str(date), "end_date": str(date), "account": qb_id}

	from urllib.parse import urlencode

	full_url = f"{report_url}?{urlencode(params)}"

	try:
		response = quickbooks_obj.make_request("GET", full_url, {})
		rows = response.get("Rows", {}).get("Row", [])
		if not rows:
			return []

		transactions = []

		def parse_gl_rows(row_list, current_account_id=None):
			for row in row_list:
				if row.get("type") == "Section":
					header = row.get("Header", {})
					col_data = header.get("ColData", [])
					new_account_id = current_account_id
					if col_data and isinstance(col_data[0], dict) and col_data[0].get("id"):
						new_account_id = str(col_data[0].get("id"))

					if "Rows" in row:
						parse_gl_rows(row["Rows"].get("Row", []), new_account_id)
					continue

				if row.get("type") == "Data":
					if current_account_id != str(qb_id):
						continue

					col_data = row.get("ColData", [])
					if len(col_data) < 7:
						continue

					# Typical GL Report columns:
					# Date(0), Transaction Type(1), No(2), Name(3), Memo/Description(4), Split(5), Amount(6), Balance(7)

					date_val = col_data[0].get("value")
					# Skip "Beginning Balance" and other header-like data rows
					if not date_val or "Balance" in date_val:
						continue

					txn = {
						"date": date_val,
						"type": col_data[1].get("value"),
						"no": col_data[2].get("value"),
						"name": col_data[3].get("value"),
						"memo": col_data[4].get("value"),
						"amount": flt(col_data[6].get("value", 0)),
					}
					transactions.append(txn)

		parse_gl_rows(rows)
		return transactions

	except Exception as e:
		_dbg("get_daily_transactions:qb_fetch_error", {"error": str(e)})
		return []


@frappe.whitelist()
def get_quickbooks_journal_details(company_name, date, account_name, amount, qb_id=None):
	"""
	Fetch the counterpart of a transaction from the QuickBooks Journal Report.
	"""
	if not company_name:
		company_name = frappe.defaults.get_user_default("company")

	# Get QuickBooks connection
	try:
		from .api import _create_quickbooks_client

		qs = frappe.get_doc("Quickbooks Settings")
		quickbooks_obj = _create_quickbooks_client(qs)
	except Exception as e:
		frappe.throw(_("Failed to connect to QuickBooks: {0}").format(str(e)))

	# Fetch Journal Report for the specific date
	report_url = quickbooks_obj.api_url + f"/company/{quickbooks_obj.company_id}/reports/JournalReport"

	params = {
		"start_date": str(date),
		"end_date": str(date),
	}

	from urllib.parse import urlencode

	full_url = f"{report_url}?{urlencode(params)}"

	try:
		response = quickbooks_obj.make_request("GET", full_url, {})
		rows = response.get("Rows", {}).get("Row", [])
		if not rows:
			return None

		# Process rows to find matching transaction
		# Columns: Date(0), Transaction Type(1), Num(2), Name(3), Memo(4), Account(5), Debit(6), Credit(7)
		target_amount = abs(flt(amount))

		all_data_rows = []

		def collect_data_rows(row_list):
			for row in row_list:
				if row.get("type") == "Section":
					if "Rows" in row:
						collect_data_rows(row["Rows"].get("Row", []))
					if "Summary" in row and "ColData" in row["Summary"]:
						# Sometimes relevant data is in summary? Usually not for matching entries.
						pass
				elif row.get("type") == "Data":
					all_data_rows.append(row)

		collect_data_rows(rows)

		for i, row in enumerate(all_data_rows):
			col_data = row.get("ColData", [])
			if len(col_data) < 8:
				continue

			row_date = col_data[0].get("value")
			row_acc_name = col_data[5].get("value")
			row_debit = flt(col_data[6].get("value", 0))
			row_credit = flt(col_data[7].get("value", 0))

			# Check if this row matches our transaction
			acc_match = False
			if account_name and row_acc_name:
				if account_name in row_acc_name or row_acc_name in account_name:
					acc_match = True

			if qb_id and isinstance(col_data[5], dict) and col_data[5].get("id") == str(qb_id):
				acc_match = True

			if acc_match and (
				abs(row_debit - target_amount) < 0.01 or abs(row_credit - target_amount) < 0.01
			):
				# Found the row! Now collect all rows for this specific Journal Entry
				matched_rows = [_format_journal_row(row)]

				# If current row has a date, it's the first row of a JE. Collect subsequent rows with no date.
				if row_date and row_date != "0-00-00":
					j = i + 1
					while j < len(all_data_rows):
						next_row = all_data_rows[j]
						next_date = next_row.get("ColData", [])[0].get("value")
						if not next_date or next_date == "0-00-00":
							matched_rows.append(_format_journal_row(next_row))
							j += 1
						else:
							break

				# If current row has no date, it's a subsequent row. Collect preceding rows back to the one with a date.
				else:
					# Collect preceding rows
					j = i - 1
					while j >= 0:
						prev_row = all_data_rows[j]
						prev_date = prev_row.get("ColData", [])[0].get("value")
						matched_rows.insert(0, _format_journal_row(prev_row))
						if prev_date and prev_date != "0-00-00":
							break
						j -= 1

					# Also collect any *subsequent* rows for the same JE (if any)
					j = i + 1
					while j < len(all_data_rows):
						next_row = all_data_rows[j]
						next_date = next_row.get("ColData", [])[0].get("value")
						if not next_date or next_date == "0-00-00":
							matched_rows.append(_format_journal_row(next_row))
							j += 1
						else:
							break

				return matched_rows

		return []

	except Exception as e:
		_dbg("get_quickbooks_journal_details:error", {"error": str(e)})
		return None


@frappe.whitelist()
def create_journal_entry_from_qb(rows, company_name):
	"""Create an ERPNext Journal Entry from QuickBooks Journal rows."""
	if isinstance(rows, str):
		import json

		rows = json.loads(rows)

	if not rows:
		frappe.throw(_("No rows provided to create Journal Entry"))

	# Filter out empty rows (no account name and zero amounts)
	rows = [r for r in rows if r.get("account") and (flt(r.get("debit")) > 0 or flt(r.get("credit")) > 0)]

	if not rows:
		frappe.throw(_("No valid rows found to create Journal Entry"))

	je = frappe.new_doc("Journal Entry")
	je.company = company_name
	je.posting_date = rows[0].get("date")
	if je.posting_date == "0-00-00" or not je.posting_date:
		# If the first row has no date, find the first valid date in the list
		for r in rows:
			if r.get("date") and r.get("date") != "0-00-00":
				je.posting_date = r.get("date")
				break

	if not je.posting_date:
		je.posting_date = frappe.utils.today()

	je.voucher_type = "Journal Entry"

	for row in rows:
		qb_account_name = row.get("account")
		qb_account_id = row.get("account_id")

		erp_account = None

		if qb_account_id:
			# Strategy 1: Match by quickbooks_account_id
			from .qb_id_utils import make_unique_qb_id

			unique_qb_account_id = make_unique_qb_id(qb_account_id, company_name)

			erp_account = frappe.db.get_value(
				"Account",
				{"company": company_name, "quickbooks_account_id": unique_qb_account_id, "is_group": 0},
				"name",
			)

		if not erp_account:
			# Strategy 2: Direct name match
			account_name_clean = (
				qb_account_name.split(" - ")[-1] if " - " in qb_account_name else qb_account_name
			)
			accs = frappe.get_all(
				"Account", filters={"company": company_name, "is_group": 0}, fields=["name", "account_name"]
			)
			for acc in accs:
				if acc.account_name == account_name_clean or acc.name == qb_account_name:
					erp_account = acc.name
					break

		if not erp_account:
			frappe.throw(
				_("Could not find a matching ERPNext account for QuickBooks account: {0} (ID: {1})").format(
					qb_account_name, qb_account_id or "N/A"
				)
			)

		je.append(
			"accounts",
			{
				"account": erp_account,
				"debit_in_account_currency": flt(row.get("debit", 0)),
				"credit_in_account_currency": flt(row.get("credit", 0)),
				"user_remark": row.get("memo") or row.get("name"),
			},
		)

	je.remark = rows[0].get("memo") or _("Converted from QuickBooks Journal")
	je.insert()
	je.submit()

	return je.name


def _format_journal_row(row):
	col_data = row.get("ColData", [])
	return {
		"date": col_data[0].get("value"),
		"type": col_data[1].get("value"),
		"no": col_data[2].get("value"),
		"name": col_data[3].get("value"),
		"memo": col_data[4].get("value"),
		"account": col_data[5].get("value"),
		"account_id": col_data[5].get("id") if isinstance(col_data[5], dict) else None,
		"debit": flt(col_data[6].get("value", 0)),
		"credit": flt(col_data[7].get("value", 0)),
	}
