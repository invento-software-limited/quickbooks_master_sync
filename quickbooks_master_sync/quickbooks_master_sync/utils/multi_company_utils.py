"""
Multi-Company Utility Functions for QuickBooks Integration

This module provides helper functions for handling multi-company scenarios
in QuickBooks to ERPNext sync operations.
"""

import frappe
from frappe import _

from .logging import qb_log_error


def get_tax_account_for_company(qb_tax_id, company):
	"""
	Get tax account for a QuickBooks tax code with multi-company support.

	Priority:
	1. Company-specific mapping (where company field matches)
	2. Global mapping (where company field is NULL or empty)

	Args:
	    qb_tax_id (str): QuickBooks Tax Code ID
	    company (str): ERPNext Company name

	Returns:
	    str: ERPNext Tax Account name or None if no mapping found

	Example:
	    >>> tax_account = get_tax_account_for_company("5", "TCIB")
	    >>> print(tax_account)
	    "VAT Output - TCIB"
	"""
	if not qb_tax_id:
		return None

	# Try company-specific mapping first (highest priority)
	if company:
		tax_account = frappe.db.get_value(
			"Quickbooks Tax Account",
			{
				"parent": "Quickbooks Settings",
				"parenttype": "Quickbooks Settings",
				"quickbooks_tax_id": qb_tax_id,
				"company": company,
			},
			"tax_account",
		)

		if tax_account:
			return tax_account


def ensure_tax_account_for_company(qb_tax_id, company=None):
	"""
	Return the mapped tax account and raise a clear error if the mapping is missing.

	Args:
	    qb_tax_id (str): QuickBooks Tax Code ID
	    company (str, optional): ERPNext Company name

	Raises:
	    frappe.ValidationError: When no mapping exists so callers get a friendly failure.
	"""
	tax_account = get_tax_account_for_company(qb_tax_id, company)
	if tax_account:
		return tax_account

	company_label = (
		company
		or frappe.defaults.get_user_default("company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or _("(all companies)")
	)

	log_message = _(
		"You did not set tax/vat account head on Quickbooks Tax Account for QuickBooks Tax Code {0} "
		"and company {1}. Please add the mapping under Quickbooks Settings > Quickbooks Tax Account."
	).format(qb_tax_id or _("(missing tax code)"), company_label)

	qb_log_error(
		title=_("Missing Tax Account Mapping"),
		method="ensure_tax_account_for_company",
		message=log_message,
		module="multi_company_utils",
		request_data={
			"quickbooks_tax_id": qb_tax_id,
			"company": company,
		},
	)


def get_tax_rate_for_company(qb_tax_id, company):
	"""
	Get tax rate for a QuickBooks tax code with multi-company support.

	Args:
	    qb_tax_id (str): QuickBooks Tax Code ID
	    company (str): ERPNext Company name

	Returns:
	    float: Tax rate percentage or None if no mapping found
	"""
	if not qb_tax_id:
		return None

	# Try company-specific mapping first
	if company:
		tax_rate = frappe.db.get_value(
			"Quickbooks Tax Account",
			{
				"parent": "Quickbooks Settings",
				"parenttype": "Quickbooks Settings",
				"quickbooks_tax_id": qb_tax_id,
				"company": company,
			},
			"tax_rate",
		)

		if tax_rate is not None:
			return tax_rate

	# Fallback to global mapping
	tax_rate = frappe.db.get_value(
		"Quickbooks Tax Account",
		{
			"parent": "Quickbooks Settings",
			"parenttype": "Quickbooks Settings",
			"quickbooks_tax_id": qb_tax_id,
			"company": ["in", [None, ""]],
		},
		"tax_rate",
	)

	return tax_rate


def get_all_tax_mappings_for_company(company=None):
	"""
	Get all tax account mappings for a company.

	Args:
	    company (str, optional): ERPNext Company name. If None, returns all mappings.

	Returns:
	    list: List of dicts containing tax mappings

	Example:
	    >>> mappings = get_all_tax_mappings_for_company("TCIB")
	    >>> for mapping in mappings:
	    ...     print(mapping.quickbooks_tax_id, "→", mapping.tax_account)
	"""
	filters = {"parent": "Quickbooks Settings", "parenttype": "Quickbooks Settings"}

	if company:
		# Get both company-specific and global mappings
		filters["company"] = ["in", [company, None, ""]]

	mappings = frappe.get_all(
		"Quickbooks Tax Account",
		filters=filters,
		fields=[
			"company",
			"quickbooks_tax_id",
			"quickbooks_tax",
			"tax_account",
			"tax_rate",
			"quickbooks_tax_rate_id",
			"quickbooks_tax_rate_name",
		],
		order_by="company DESC",  # Company-specific first, then global
	)

	return mappings


def validate_tax_mapping_for_company(qb_tax_id, company):
	"""
	Validate if a tax mapping exists for a QuickBooks tax code and company.

	Args:
	    qb_tax_id (str): QuickBooks Tax Code ID
	    company (str): ERPNext Company name

	Returns:
	    dict: {
	        "valid": bool,
	        "mapping_type": "company" | "global" | None,
	        "tax_account": str or None,
	        "message": str
	    }
	"""
	result = {"valid": False, "mapping_type": None, "tax_account": None, "message": ""}

	if not qb_tax_id:
		result["message"] = "QuickBooks Tax Code ID is required"
		return result

	# Check company-specific mapping
	if company:
		tax_account = frappe.db.get_value(
			"Quickbooks Tax Account",
			{
				"parent": "Quickbooks Settings",
				"parenttype": "Quickbooks Settings",
				"quickbooks_tax_id": qb_tax_id,
				"company": company,
			},
			"tax_account",
		)

		if tax_account:
			result["valid"] = True
			result["mapping_type"] = "company"
			result["tax_account"] = tax_account
			result["message"] = f"Found company-specific mapping for {company}"
			return result

	# Check global mapping
	tax_account = frappe.db.get_value(
		"Quickbooks Tax Account",
		{
			"parent": "Quickbooks Settings",
			"parenttype": "Quickbooks Settings",
			"quickbooks_tax_id": qb_tax_id,
			"company": ["in", [None, ""]],
		},
		"tax_account",
	)

	if tax_account:
		result["valid"] = True
		result["mapping_type"] = "global"
		result["tax_account"] = tax_account
		result["message"] = "Found global mapping (applies to all companies)"
		return result

	# No mapping found
	result["message"] = f"No tax mapping found for QuickBooks Tax Code ID: {qb_tax_id}"
	if company:
		result["message"] += f" in company {company} or globally"

	return result


def create_or_update_tax_mapping(qb_tax_id, qb_tax_name, tax_account, company=None, tax_rate=None):
	"""
	Create or update a tax account mapping in QuickBooks Settings.

	Args:
	    qb_tax_id (str): QuickBooks Tax Code ID
	    qb_tax_name (str): QuickBooks Tax Name
	    tax_account (str): ERPNext Tax Account name
	    company (str, optional): ERPNext Company name (None for global)
	    tax_rate (float, optional): Tax rate percentage

	Returns:
	    str: Success message
	"""
	# Get QuickBooks Settings doc
	qb_settings = frappe.get_doc("Quickbooks Settings")

	# Check if mapping already exists
	existing = None
	for tax in qb_settings.taxes:
		if tax.quickbooks_tax_id == qb_tax_id and tax.company == company:
			existing = tax
			break

	if existing:
		# Update existing mapping
		existing.quickbooks_tax = qb_tax_name
		existing.tax_account = tax_account
		if tax_rate is not None:
			existing.tax_rate = tax_rate
		message = f"Updated tax mapping for {qb_tax_name}"
	else:
		# Create new mapping
		qb_settings.append(
			"taxes",
			{
				"company": company,
				"quickbooks_tax_id": qb_tax_id,
				"quickbooks_tax": qb_tax_name,
				"tax_account": tax_account,
				"tax_rate": tax_rate,
			},
		)
		message = f"Created tax mapping for {qb_tax_name}"

	qb_settings.save(ignore_permissions=True)
	frappe.db.commit()

	company_str = f" for company {company}" if company else " (global)"
	return message + company_str


def get_selling_price_list_for_company(company=None):
	"""
	Get selling price list for a company with multi-company support.

	Priority:
	1. Company-specific setting (from Quickbooks Company Settings table)
	2. ERPNext Selling Settings default

	Args:
	    company (str, optional): ERPNext Company name

	Returns:
	    str: Price List name or None if not found

	Example:
	    >>> price_list = get_selling_price_list_for_company("TCIB")
	    >>> print(price_list)
	    "Standard Selling"
	"""
	# Try company-specific setting first
	if company:
		price_list = frappe.db.get_value(
			"Quickbooks Company Settings",
			{"parent": "Quickbooks Settings", "parenttype": "Quickbooks Settings", "company": company},
			"selling_price_list",
		)

		if price_list:
			return price_list

	# Fallback to ERPNext Selling Settings
	try:
		selling_settings = frappe.get_single("Selling Settings")
		if selling_settings.selling_price_list:
			return selling_settings.selling_price_list
	except Exception:
		pass

	return None


def get_buying_price_list_for_company(company=None):
	"""
	Get buying price list for a company with multi-company support.

	Priority:
	1. Company-specific setting (from Quickbooks Company Settings table)
	2. ERPNext Buying Settings default

	Args:
	    company (str, optional): ERPNext Company name

	Returns:
	    str: Price List name or None if not found

	Example:
	    >>> price_list = get_buying_price_list_for_company("TCIB")
	    >>> print(price_list)
	    "Standard Buying"
	"""
	# Try company-specific setting first
	if company:
		price_list = frappe.db.get_value(
			"Quickbooks Company Settings",
			{"parent": "Quickbooks Settings", "parenttype": "Quickbooks Settings", "company": company},
			"buying_price_list",
		)

		if price_list:
			return price_list

	# Fallback to ERPNext Buying Settings
	try:
		buying_settings = frappe.get_single("Buying Settings")
		if buying_settings.buying_price_list:
			return buying_settings.buying_price_list
	except Exception:
		pass

	return None


def get_warehouse_for_company(company=None):
	"""
	Get warehouse for a company with multi-company support.

	Priority:
	1. Company-specific setting (from Quickbooks Company Settings table)
	2. ERPNext Stock Settings default warehouse
	3. Any warehouse for the company

	Args:
	    company (str, optional): ERPNext Company name

	Returns:
	    str: Warehouse name or None if not found

	Example:
	    >>> warehouse = get_warehouse_for_company("TCIB")
	    >>> print(warehouse)
	    "Stores - TCIB"
	"""
	# Try company-specific setting first
	if company:
		warehouse = frappe.db.get_value(
			"Quickbooks Company Settings",
			{"parent": "Quickbooks Settings", "parenttype": "Quickbooks Settings", "company": company},
			"warehouse",
		)

		if warehouse:
			return warehouse

	# Fallback to ERPNext Stock Settings
	if company:
		try:
			stock_settings_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
			if stock_settings_warehouse:
				# Verify it belongs to the correct company
				warehouse_company = frappe.db.get_value("Warehouse", stock_settings_warehouse, "company")
				if warehouse_company == company:
					return stock_settings_warehouse
		except Exception:
			pass

		# Last resort: find any warehouse for this company
		try:
			any_warehouse = frappe.db.get_value(
				"Warehouse", {"company": company, "is_group": 0}, "name", order_by="creation asc"
			)
			if any_warehouse:
				return any_warehouse
		except Exception:
			pass

	return None


def get_company_settings(company=None):
	"""
	Get all company-specific settings in one call.

	Args:
	    company (str, optional): ERPNext Company name

	Returns:
	    dict: {
	        "selling_price_list": str or None,
	        "buying_price_list": str or None,
	        "warehouse": str or None
	    }
	"""
	return {
		"selling_price_list": get_selling_price_list_for_company(company),
		"buying_price_list": get_buying_price_list_for_company(company),
		"warehouse": get_warehouse_for_company(company),
	}


# Example usage and test functions
def test_multi_company_tax_lookup():
	"""
	Test function to demonstrate multi-company tax account lookup.
	Run in ERPNext Console to test.
	"""
	print("\\n=== Testing Multi-Company Tax Account Lookup ===\\n")

	# Test 1: Company-specific lookup
	print("Test 1: Company-specific tax mapping")
	tax_account = get_tax_account_for_company("5", "TCIB")
	print(f"QB Tax ID '5' for TCIB → {tax_account or 'Not found'}")

	# Test 2: Global lookup
	print("\\nTest 2: Global tax mapping")
	tax_account = get_tax_account_for_company("NON", None)
	print(f"QB Tax ID 'NON' (global) → {tax_account or 'Not found'}")

	# Test 3: Validation
	print("\\nTest 3: Validate tax mapping")
	result = validate_tax_mapping_for_company("5", "TCIB")
	print(f"Valid: {result['valid']}")
	print(f"Type: {result['mapping_type']}")
	print(f"Account: {result['tax_account']}")
	print(f"Message: {result['message']}")

	# Test 4: Get all mappings
	print("\\nTest 4: Get all mappings for TCIB")
	mappings = get_all_tax_mappings_for_company("TCIB")
	for mapping in mappings[:5]:  # Show first 5
		company_str = mapping.company or "(global)"
		print(f"  {mapping.quickbooks_tax_id} [{company_str}] → {mapping.tax_account}")

	print("\\n=== Tests Complete ===\\n")
