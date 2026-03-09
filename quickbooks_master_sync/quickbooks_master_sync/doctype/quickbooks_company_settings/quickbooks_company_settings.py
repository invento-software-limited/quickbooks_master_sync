# Copyright (c) 2025, Invento Software Limited and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document


class QuickbooksCompanySettings(Document):
	def validate(self):
		"""Validate QuickBooks Company Settings to prevent duplicates."""
		self.validate_duplicate_company()

	def validate_duplicate_company(self):
		"""
		Prevent duplicate company entries in the same parent document.
		"""
		if not self.company:
			return

		# Build filter for checking duplicates
		filters = {
			"parent": self.parent,
			"parenttype": self.parenttype,
			"company": self.company,
			"name": ["!=", self.name]  # Exclude current row when updating
		}

		# Check if duplicate exists
		existing = frappe.db.get_value(
			"Quickbooks Company Settings",
			filters,
			"name"
		)

		if existing:
			frappe.throw(
				_("Company '{0}' already has settings configured. Please update the existing entry instead of creating a duplicate.").format(
					self.company
				)
			)
