from .base import FromJsonMixin, QuickbooksBaseObject, QuickbooksTransactionEntity, Ref, ToJsonMixin
from .detailline import DetailLine


class InventoryAdjustmentLineDetail(QuickbooksBaseObject):
	class_dict = {
		"ItemRef": Ref,
		"ClassRef": Ref,
	}

	def __init__(self):
		super().__init__()
		self.ItemRef = None
		self.ClassRef = None
		self.QtyDiff = 0
		self.NewQty = 0


class InventoryAdjustmentLine(DetailLine):
	class_dict = {"InventoryAdjustmentLineDetail": InventoryAdjustmentLineDetail}

	def __init__(self):
		super().__init__()
		self.DetailType = "InventoryAdjustmentLineDetail"
		self.InventoryAdjustmentLineDetail = None


class InventoryAdjustment(QuickbooksTransactionEntity):
	class_dict = {
		"AccountRef": Ref,
		"CustomerRef": Ref,
		"ClassRef": Ref,
	}

	list_dict = {"Line": InventoryAdjustmentLine}

	qbo_object_name = "InventoryAdjustment"

	def __init__(self):
		super().__init__()
		self.AccountRef = None
		self.CustomerRef = None
		self.ClassRef = None
		self.Line = []
		self.AdjustmentDate = ""
		self.PrivateNote = ""
