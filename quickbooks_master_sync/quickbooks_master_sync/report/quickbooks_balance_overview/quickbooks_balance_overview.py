import json

import frappe
from frappe import _
from frappe.utils import flt, today

from quickbooks_master_sync.quickbooks_master_sync.api import _create_quickbooks_client
from quickbooks_master_sync.quickbooks_master_sync.sync.compare_balances import (
	_compare_balances,
	_fetch_erpnext_balances,
	_fetch_quickbooks_balances,
)


def execute(filters=None):
	if not filters:
		filters = {}

	as_of_date = filters.get("as_of_date") or today()
	tolerance = filters.get("tolerance") or 0.01

	columns = get_columns(filters)
	data = get_data(as_of_date, tolerance)

	return columns, data


def get_columns(filters):
	return [
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 200,
		},
		{"fieldname": "matched_accounts", "label": _("Matched Accounts"), "fieldtype": "Int", "width": 150},
		{
			"fieldname": "mismatched_accounts",
			"label": _("Mismatched Accounts"),
			"fieldtype": "Int",
			"width": 150,
		},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Data", "hidden": 1},
		# Asset
		{
			"fieldname": "total_asset_qb",
			"label": _("Asset QB"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_asset_erp",
			"label": _("Asset ERP"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_asset_diff",
			"label": _("Asset Diff"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		# Liability
		{
			"fieldname": "total_liability_qb",
			"label": _("Liability QB"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_liability_erp",
			"label": _("Liability ERP"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_liability_diff",
			"label": _("Liability Diff"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		# Equity
		{
			"fieldname": "total_equity_qb",
			"label": _("Equity QB"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_equity_erp",
			"label": _("Equity ERP"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_equity_diff",
			"label": _("Equity Diff"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		# Income
		{
			"fieldname": "total_income_qb",
			"label": _("Income QB"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_income_erp",
			"label": _("Income ERP"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_income_diff",
			"label": _("Income Diff"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		# Expense
		{
			"fieldname": "total_expense_qb",
			"label": _("Expense QB"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_expense_erp",
			"label": _("Expense ERP"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "total_expense_diff",
			"label": _("Expense Diff"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "view_details",
			"label": _("Action"),
			"fieldtype": "Data",
			"width": 120,
			"align": "center",
		},
	]


def get_data(as_of_date, tolerance):
	data = []

	# Get Quickbooks Settings
	# Quickbooks Settings is a Single DocType, so we can just get it.
	try:
		qs = frappe.get_doc("Quickbooks Settings")
	except Exception:
		frappe.msgprint(_("Quickbooks Settings not found"))
		return []

	if not qs.enable_quickbooks_online:
		frappe.msgprint(_("QuickBooks Online is not enabled."))
		return []

	# Initialize QB Client (Once)
	try:
		quickbooks_obj = _create_quickbooks_client(qs)
	except Exception as e:
		frappe.msgprint(_("Failed to connect to QuickBooks: {0}").format(str(e)))
		return []

	# Fetch QB Balances (Once - assuming all companies map to same realm/QBO instance)
	# Note: If different companies map to DIFFERENT realms, this optimization is invalid.
	# But usually Quickbooks Settings acts for single QBO Company -> Multiple ERP Companies mapping (e.g. Branches?)
	# Or strict 1:1.
	# Based on `company_settings` table, multiple ERP companies can be configured.
	# Assuming they all relate to the SAME connected QBO account (defined by Realm ID in parent doc).

	# We pass the default company name just for logging, the QB client is already tied to a specific Company ID.
	default_company = frappe.defaults.get_user_default("company")
	if qs.company_settings:
		default_company = qs.company_settings[0].company
	if not default_company:
		default_company = "Global"

	try:
		qb_balances = _fetch_quickbooks_balances(quickbooks_obj, default_company, as_of_date)
	except Exception as e:
		frappe.log_error(f"Failed to fetch QB balances: {e!s}", "QuickBooks Balance Overview")
		return []

	# Loop through configured companies
	if not qs.get("company_settings"):
		frappe.msgprint(_("No companies configured in QuickBooks Settings."))
		return []

	for row in qs.company_settings:
		company = row.company
		company_currency = frappe.get_cached_value("Company", company, "default_currency")

		# Fetch ERPNext Balances for this company
		erp_balances = _fetch_erpnext_balances(company, as_of_date)

		# Compare
		comparison = _compare_balances(qb_balances, erp_balances, tolerance)
		summary = comparison.get("summary", {})

		total_difference = summary.get("total_difference", 0.0)

		# We pass raw values if fieldtype is Currency; report view handles formatting based on "currency" field.

		# Button logic
		view_details_html = """<button class="btn btn-xs btn-default" onclick='frappe.route_options={{"company": "{}", "as_of_date": "{}", "run": 1}}; frappe.set_route("quickbooks-balance-comparison");'>{}</button>""".format(
			company, as_of_date, _("View Details")
		)

		row_data = {
			"company": company,
			"currency": company_currency,  # Needed for formatting
			"matched_accounts": summary.get("matched_accounts", 0),
			"mismatched_accounts": summary.get("mismatched_accounts", 0),
			"total_difference": total_difference,
			"view_details": view_details_html,
		}

		# Add detailed categories (qb, erp, diff)
		# Note: In backend keys are "total_asset_qb", etc.
		categories = ["asset", "liability", "equity", "income", "expense"]
		for cat in categories:
			row_data[f"total_{cat}_qb"] = summary.get(f"total_{cat}_qb", 0.0)
			row_data[f"total_{cat}_erp"] = summary.get(f"total_{cat}_erp", 0.0)
			row_data[f"total_{cat}_diff"] = summary.get(f"total_{cat}_diff", 0.0)

		data.append(row_data)

	return data
