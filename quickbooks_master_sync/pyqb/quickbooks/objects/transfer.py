from six import python_2_unicode_compatible

from .base import LinkedTxnMixin, QuickbooksManagedObject, QuickbooksTransactionEntity, Ref


@python_2_unicode_compatible
class Transfer(QuickbooksManagedObject, QuickbooksTransactionEntity, LinkedTxnMixin):
	"""
	QBO definition: A Transfer represents a transaction where funds are moved between two accounts from the
	company's QuickBooks chart of accounts.
	"""

	class_dict = {
		"FromAccountRef": Ref,
		"ToAccountRef": Ref,
	}

	qbo_object_name = "Transfer"

	def __init__(self):
		super().__init__()
		self.Amount = 0
		self.TxnDate = ""
		self.PrivateNote = ""
		self.TxnSource = ""

		self.FromAccountRef = None
		self.ToAccountRef = None

	def __str__(self):
		return str(self.Amount)
