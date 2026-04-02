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
