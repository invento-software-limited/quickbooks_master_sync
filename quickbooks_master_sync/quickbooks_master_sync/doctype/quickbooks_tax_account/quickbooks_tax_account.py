# Copyright (c) 2025, Invento Software Limited and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document


class QuickbooksTaxAccount(Document):
	def before_save(self):
		"""Ensure unique_key is always set before saving."""
		self.set_unique_key()

	def validate(self):
		"""Validate QuickBooks Tax Account mapping to prevent duplicates."""
		self.validate_duplicate_mapping()
		self.set_unique_key()  # Ensure it's set during validation too

	def validate_duplicate_mapping(self):
		"""
		Prevent duplicate (quickbooks_tax_id, company) combinations.

		Rules:
		- Same QB Tax ID can be mapped to different companies (multi-company support)
		- Same QB Tax ID cannot be mapped twice to the same company
		- Global mappings (company=NULL or empty) are unique per QB Tax ID
		"""
		if not self.quickbooks_tax_id:
			return

		# Normalize company field: empty string becomes None for consistency
		company_value = self.company if self.company else None

		# Build filter for checking duplicates
		filters = {
			"parent": self.parent,
			"parenttype": self.parenttype,
			"quickbooks_tax_id": self.quickbooks_tax_id,
			"name": ["!=", self.name],  # Exclude current row when updating
		}

		# Check for company-specific or global mapping duplicates
		if company_value:
			# For company-specific mapping, check if same (tax_id, company) exists
			filters["company"] = company_value
			error_msg = _("QuickBooks Tax ID '{0}' is already mapped for company '{1}'").format(
				self.quickbooks_tax_id, company_value
			)
		else:
			# For global mapping, check if another global mapping exists
			# Check for both None and empty string
			filters["company"] = ["in", [None, ""]]
			error_msg = _(
				"QuickBooks Tax ID '{0}' already has a global mapping (no company specified)"
			).format(self.quickbooks_tax_id)

		# Check if duplicate exists
		existing = frappe.db.get_value(
			"Quickbooks Tax Account",
			filters,
			["name", "quickbooks_tax", "tax_account", "company"],
			as_dict=True,
		)

		if existing:
			existing_company = existing.company or _("Global (all companies)")
			frappe.throw(
				_(
					"{0}.<br><br>Existing mapping: <b>{1}</b> → <b>{2}</b> (Company: {3})<br><br>"
					"Please update the existing mapping instead of creating a duplicate."
				).format(
					error_msg,
					existing.quickbooks_tax or self.quickbooks_tax_id,
					existing.tax_account or "Not Set",
					existing_company,
				),
				title=_("Duplicate Tax Mapping"),
			)

	def set_unique_key(self):
		"""
		Set a composite unique key for easier identification in grid view.
		Format: QB_TAX_ID-COMPANY_ABBR or QB_TAX_ID-GLOBAL (for global)
		"""
		if not self.quickbooks_tax_id:
			self.unique_key = ""
			return

		# Normalize company field: empty string becomes None for consistency
		company_value = self.company if self.company else None

		if company_value:
			# Get company abbreviation
			try:
				company_abbr = frappe.db.get_value("Company", company_value, "abbr")
				if company_abbr:
					self.unique_key = f"{self.quickbooks_tax_id}-{company_abbr}"
				else:
					# Fallback: use first 3 characters of company name
					self.unique_key = f"{self.quickbooks_tax_id}-{company_value[:3].upper()}"
			except Exception:
				# If company lookup fails, use company name directly
				self.unique_key = f"{self.quickbooks_tax_id}-{company_value[:3].upper()}"
		else:
			# Global mapping (no company)
			self.unique_key = f"{self.quickbooks_tax_id}-GLOBAL"
