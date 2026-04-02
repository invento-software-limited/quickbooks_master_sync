from .base import QuickbooksBaseObject


class CreditChargeInfo(QuickbooksBaseObject):
	class_dict = {}

	def __init__(self):
		super().__init__()
		self.Type = ""
		self.NameOnAcct = ""
		self.CcExpiryMonth = 0
		self.CcExpiryYear = 0
		self.BillAddrStreet = ""
		self.PostalCode = ""
		self.Amount = 0
		self.ProcessPayment = False


class CreditChargeResponse(QuickbooksBaseObject):
	def __init__(self):
		super().__init__()

		self.CCTransId = ""
		self.AuthCode = ""
		self.TxnAuthorizationTime = ""
		self.Status = ""


class CreditCardPayment(QuickbooksBaseObject):
	class_dict = {"CreditChargeInfo": CreditChargeInfo, "CreditChargeResponse": CreditChargeResponse}

	def __init__(self):
		super().__init__()
		self.CreditChargeInfo = None
		self.CreditChargeResponse = None
