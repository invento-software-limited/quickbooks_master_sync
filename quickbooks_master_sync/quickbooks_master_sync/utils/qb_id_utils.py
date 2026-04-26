"""
QuickBooks ID Utilities for Multi-Company Support

This module provides utilities to make QuickBooks IDs unique across multiple companies
by prefixing them with company abbreviation.

Format: {COMPANY_ABBR}-{QB_ID}
Example: "BISB-3087" instead of "3087"
"""

import frappe


def make_unique_qb_id(qb_id, company, prefix=None):
	"""
	Create a unique QuickBooks ID by prefixing with company abbreviation.

	Args:
	    qb_id (str): Original QuickBooks ID (e.g., "3087")
	    company (str): Company name (e.g., "BOP Innovation Services Bangladesh Pvt Ltd")
	    prefix (str, optional): Additional prefix (e.g., "CM-", "VC-"). Defaults to None.

	Returns:
	    str: Unique QB ID (e.g., "BISB-3087" or "BISB-CM-3087")
	"""
	if not qb_id:
		return None

	if not company:
		# If no company provided, return original ID (backward compatibility)
		# If prefix is provided, append it? No, logic depends on company abbr.
		# But if we strictly want the pattern, we might need to handle it.
		# For now, sticking to existing behavior + simple prefixing if no company?
		# Typically this function is called WITH company in our context.
		if prefix:
			return f"{prefix}{qb_id}"
		return str(qb_id)

	# Get company abbreviation
	try:
		abbr = frappe.db.get_value("Company", company, "abbr")
		if not abbr:
			# Company not found, return original ID
			frappe.log_error(f"Company abbreviation not found for: {company}", "make_unique_qb_id")
			if prefix:
				return f"{prefix}{qb_id}"
			return str(qb_id)

		qb_id_str = str(qb_id)

		# If prefix is provided, we want {abbr}-{prefix}{qb_id_str}
		# Example: BISB-CM-3087

		if prefix:
			# Check if already has the FULL pattern (idempotent)
			full_prefix = f"{abbr}-{prefix}"
			if qb_id_str.startswith(full_prefix):
				return qb_id_str

			# Use EXACT construction, ignoring if qb_id_str starts with abbr- only
			# This is key: "CM-123" with company "CM" -> "CM-CM-123"
			return f"{abbr}-{prefix}{qb_id_str}"

		# Standard case (no prefix argument)
		# Check if ID already has company prefix (idempotent)
		if qb_id_str.startswith(f"{abbr}-"):
			return qb_id_str

		# Create unique ID with company prefix
		return f"{abbr}-{qb_id_str}"

	except Exception as e:
		frappe.log_error(
			f"Error creating unique QB ID for {qb_id} and company {company}: {e!s}", "make_unique_qb_id"
		)
		return str(qb_id)


def parse_qb_id(unique_qb_id):
	"""
	Extract original QuickBooks ID from unique ID.

	Args:
	    unique_qb_id (str): Unique QB ID (e.g., "BISB-3087")

	Returns:
	    tuple: (company_abbr, original_qb_id) or (None, unique_qb_id) if no prefix

	Examples:
	    >>> parse_qb_id("BISB-3087")
	    ("BISB", "3087")

	    >>> parse_qb_id("3087")  # Old format without prefix
	    (None, "3087")
	"""
	if not unique_qb_id:
		return None, None

	unique_qb_id_str = str(unique_qb_id)

	# Check if it has company prefix
	if "-" in unique_qb_id_str:
		parts = unique_qb_id_str.split("-", 1)
		if len(parts) == 2:
			return parts[0], parts[1]

	# No prefix found, return as-is
	return None, unique_qb_id_str


def get_company_from_qb_id(unique_qb_id):
	"""
	Get company name from unique QuickBooks ID.

	Args:
	    unique_qb_id (str): Unique QB ID (e.g., "BISB-3087")

	Returns:
	    str: Company name or None if not found

	Examples:
	    >>> get_company_from_qb_id("BISB-3087")
	    "BOP Innovation Services Bangladesh Pvt Ltd"
	"""
	abbr, _ = parse_qb_id(unique_qb_id)
	if not abbr:
		return None

	try:
		company = frappe.db.get_value("Company", {"abbr": abbr}, "name")
		return company
	except Exception:
		return None


def migrate_qb_id(doctype, qb_id_field, company_field="company"):
	"""
	Utility to migrate existing QB IDs to unique format.

	THIS IS FOR MIGRATION ONLY - Run once to update existing data.

	Args:
	    doctype (str): DocType name (e.g., "Sales Invoice")
	    qb_id_field (str): QB ID field name (e.g., "quickbooks_invoce_id")
	    company_field (str): Company field name (default: "company")

	Example:
	    >>> migrate_qb_id("Sales Invoice", "quickbooks_invoce_id", "company")
	    Updated 150 records
	"""
	try:
		# Get all records with QB IDs that don't have company prefix
		records = frappe.get_all(
			doctype,
			filters=[
				[doctype, qb_id_field, "!=", ""],
				[doctype, qb_id_field, "not like", "%-%"],  # Don't have company prefix yet
			],
			fields=["name", qb_id_field, company_field],
		)

		updated_count = 0
		for record in records:
			original_qb_id = record.get(qb_id_field)
			company = record.get(company_field)

			if original_qb_id and company:
				unique_qb_id = make_unique_qb_id(original_qb_id, company)
				if unique_qb_id != original_qb_id:
					frappe.db.set_value(
						doctype, record.name, qb_id_field, unique_qb_id, update_modified=False
					)
					updated_count += 1

		frappe.db.commit()  # nosemgrep
		print(f"Updated {updated_count} records in {doctype}")
		return updated_count

	except Exception as e:
		frappe.log_error(f"Error migrating QB IDs for {doctype}: {e!s}", "migrate_qb_id")
		raise
