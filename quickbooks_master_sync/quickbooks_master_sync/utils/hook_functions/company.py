from erpnext.setup.doctype.company.company import Company


class CustomCompany(Company):
	def create_default_accounts(self):
		if not self.flags.from_quickbooks:
			return super().create_default_accounts()

	def create_default_cost_center(self):
		if not self.flags.from_quickbooks:
			return super().create_default_cost_center()

	def create_default_tax_template(self):
		if not self.flags.from_quickbooks:
			return super().create_default_tax_template()

	def on_update(self):
		if not self.flags.from_quickbooks:
			return super().on_update()

		import erpnext.setup.doctype.company.company

		original_sync = erpnext.setup.doctype.company.company.sync_financial_report_templates
		erpnext.setup.doctype.company.company.sync_financial_report_templates = lambda *args, **kwargs: None

		try:
			return super().on_update()
		finally:
			erpnext.setup.doctype.company.company.sync_financial_report_templates = original_sync
