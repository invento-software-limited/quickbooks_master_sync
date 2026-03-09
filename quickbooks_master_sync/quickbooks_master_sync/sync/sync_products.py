from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cstr, flt, nowdate

from quickbooks_master_sync.pyqb.quickbooks.batch import batch_create
from quickbooks_master_sync.pyqb.quickbooks.objects.item import Item

from quickbooks_master_sync.quickbooks_master_sync.utils.logging import qb_log_error, qb_log_exception, qb_log_status
from quickbooks_master_sync.quickbooks_master_sync.utils.qb_id_utils import make_unique_qb_id
from .sync_utils import _dbg as _dbg_common
from .sync_utils import _get_quickbooks_company as _get_quickbooks_company_base
from .sync_utils import query_with_pagination, save_qb_data_to_json


def _dbg(event, payload=None):
    """Debug logging helper for product sync"""
    _dbg_common("sync_products", event, payload)


def _get_quickbooks_company(quickbooks_obj=None, qb_company=None, section="sync_products"):
    """Wrapper for sync_products module"""
    return _get_quickbooks_company_base(
        quickbooks_obj=quickbooks_obj,
        qb_company=qb_company,
        module_name=section,
    )


def _ensure_item_group(group_name, parent_group=None):
    """Ensure Item Group exists, create if it doesn't"""
    if not group_name:
        return None

    # Normalize group name (remove translation wrapper if present)
    if hasattr(group_name, 'encode'):
        # It's a string, check if it's a translated string
        group_name = str(group_name)

    # Handle Service/Services mismatch - check both
    if group_name == "Service":
        # Check if "Services" (plural) exists
        if frappe.db.exists("Item Group", "Services"):
            return "Services"
        # Check if "Service" (singular) exists
        if frappe.db.exists("Item Group", "Service"):
            return "Service"
    elif group_name == "Services":
        # Check if "Services" (plural) exists
        if frappe.db.exists("Item Group", "Services"):
            return "Services"
        # Check if "Service" (singular) exists
        if frappe.db.exists("Item Group", "Service"):
            return "Service"

    # Check if item group already exists (case-insensitive search by name)
    existing_group = frappe.db.get_value("Item Group", {"item_group_name": group_name}, "name")
    if existing_group:
        # If it exists, check if we need to update parent relationship
        if parent_group:
            existing_parent = frappe.db.get_value("Item Group", existing_group, "parent_item_group")
            # Resolve parent_group name to actual Item Group name
            resolved_parent = None
            if frappe.db.exists("Item Group", parent_group):
                resolved_parent = parent_group
            else:
                resolved_parent = frappe.db.get_value("Item Group", {"item_group_name": parent_group}, "name")

            if resolved_parent and existing_parent != resolved_parent:
                # Update parent relationship
                try:
                    item_group_doc = frappe.get_doc("Item Group", existing_group)
                    item_group_doc.parent_item_group = resolved_parent
                    item_group_doc.save(ignore_permissions=True)
                    frappe.db.commit()
                    _dbg("_ensure_item_group:updated_parent", {"name": group_name, "parent": resolved_parent})
                except Exception as e:
                    _dbg("_ensure_item_group:update_parent_error", {"name": group_name, "error": str(e)})
        return existing_group

    # Also check by name field directly
    if frappe.db.exists("Item Group", group_name):
        # Similar parent update logic
        if parent_group:
            existing_parent = frappe.db.get_value("Item Group", group_name, "parent_item_group")
            resolved_parent = None
            if frappe.db.exists("Item Group", parent_group):
                resolved_parent = parent_group
            else:
                resolved_parent = frappe.db.get_value("Item Group", {"item_group_name": parent_group}, "name")

            if resolved_parent and existing_parent != resolved_parent:
                try:
                    item_group_doc = frappe.get_doc("Item Group", group_name)
                    item_group_doc.parent_item_group = resolved_parent
                    item_group_doc.save(ignore_permissions=True)
                    # Note: Commit will be batched in create_Item() for better performance
                    _dbg("_ensure_item_group:updated_parent_by_name", {"name": group_name, "parent": resolved_parent})
                except Exception as e:
                    _dbg("_ensure_item_group:update_parent_by_name_error", {"name": group_name, "error": str(e)})
        return group_name

    # Create new item group
    try:
        item_group = frappe.new_doc("Item Group")
        item_group.item_group_name = group_name
        if parent_group:
            # Ensure parent exists first
            parent_exists = frappe.db.exists("Item Group", parent_group)
            if not parent_exists:
                # Try to find by name
                parent_by_name = frappe.db.get_value("Item Group", {"item_group_name": parent_group}, "name")
                if parent_by_name:
                    parent_group = parent_by_name
                else:
                    # Create parent if it doesn't exist
                    parent_group = _ensure_item_group(parent_group)

            if parent_group and frappe.db.exists("Item Group", parent_group):
                item_group.parent_item_group = parent_group

        item_group.insert(ignore_permissions=True)
        # Note: Commit will be batched in create_Item() for better performance
        _dbg("_ensure_item_group:created", {"name": group_name, "parent": parent_group})
        return item_group.name
    except frappe.exceptions.DuplicateEntryError:
        # Item group was created between check and insert
        existing = frappe.db.get_value("Item Group", {"item_group_name": group_name}, "name")
        if existing:
            return existing
        return group_name
    except Exception as e:
        _dbg("_ensure_item_group:error", {"name": group_name, "error": str(e)})
        # If creation fails, try to find by name
        existing = frappe.db.get_value("Item Group", {"item_group_name": group_name}, "name")
        if existing:
            return existing
        # Return the name anyway (might exist with different case or will be created later)
        return group_name



def get_company_stores_warehouse(company):
    """Returns the 'Stores' warehouse for the company, creating it if missing. Uses caching."""
    if not company:
        return None
        
    cache_key = f"stores_warehouse:{company}"
    warehouse = frappe.cache().get_value(cache_key)
    
    if not warehouse:
        warehouse = frappe.db.get_value("Warehouse", {"warehouse_name": "Stores", "company": company}, "name")
        
        if not warehouse:
            try:
                # Create if not exists
                w = frappe.new_doc("Warehouse")
                w.warehouse_name = "Stores"
                w.company = company
                # We need a parent warehouse if hierarchy is enforced, but for now try inserting directly
                # If "All Warehouses" exists for company, use it as parent
                parent_warehouse = frappe.db.get_value("Warehouse", {"is_group": 1, "company": company}, "name")
                if parent_warehouse:
                    w.parent_warehouse = parent_warehouse
                    
                w.insert(ignore_permissions=True)
                warehouse = w.name
                _dbg("get_company_stores_warehouse:created", {"company": company, "warehouse": warehouse})
            except Exception as e:
                _dbg("get_company_stores_warehouse:error", {"company": company, "error": str(e)})
                return None
            
        frappe.cache().set_value(cache_key, warehouse)
        
    return warehouse


def create_Item(quickbooks_obj, qb_company=None):
    """Fetch Item data from QuickBooks and store in ERPNEXT using simplified logic
    
    Refactored to use user's logic loop while maintaining existing app infrastructure.
    """

    _dbg("create_Item:start")

    quickbooks_item_list = []
    stats = {
        "total": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "failed_items": []
    }

    item_query = """SELECT * FROM Item"""
    # Use pagination helper to fetch all items
    item_list = query_with_pagination(
        quickbooks_obj,
        item_query,
        "Item",
        module_name="sync_products",
        data_name="items_list"
    )
    _dbg("create_Item:fetched_all", {"count": len(item_list)})
    stats["total"] = len(item_list)

    # Get company once for all items
    resolved_company = _get_quickbooks_company(quickbooks_obj, qb_company)
    _dbg(
        "create_Item:resolved_company",
        {
            "resolved_company": resolved_company,
            "qb_company": qb_company,
            "from_qb_company": bool(qb_company),
        },
    )
    if not resolved_company:
        raise Exception(
            "No Company found to assign to Items. Please sync company first or set a default company."
        )

    # Get company abbreviation for unique item codes
    company_abbr = frappe.db.get_value("Company", {"name": resolved_company}, "abbr")
    if not company_abbr:
        company_abbr = resolved_company[:4].upper() if resolved_company else ""
    _dbg("create_Item:company_abbr", {"company": resolved_company, "abbr": company_abbr})

    try:
        # item_list is already a list from pagination helper
        items = item_list
        if not isinstance(items, list):
            items = [items] if items else []
            
        # Initialize generic Import Tracker
        from quickbooks_master_sync.quickbooks_master_sync.utils.import_tracker import ImportTracker
        tracker = ImportTracker(len(items), "qb_product_import.log", module_name="PRODUCT", company=resolved_company)
        
        # Helper to get warehouse once per run if possible, but we use cached function inside loop
        # stores_warehouse = get_company_stores_warehouse(resolved_company)
        
        for fields in items:
            item_code = fields.get("Name")
            qb_id = fields.get("Id")
            
            if not item_code or not qb_id:
                continue

            # Generate unique QB ID for storage/lookup
    
            unique_qb_id = make_unique_qb_id(qb_id, resolved_company)
            
            # Log start
            tracker_id = f"{unique_qb_id} ({item_code})"
            tracker.log_processing("QuickBooks", tracker_id)

            # 1. Match by quickbooks_item_id (using unique ID)
            # Use 'quickbooks_item_id' field as per app standard
            existing_item_name = frappe.db.get_value("Item", {"quickbooks_item_id": unique_qb_id}, "name")
            
            # 2. Fallback to name (scoped to company if possible, but item_code is unique globally)
            if not existing_item_name:
                # Try to find by item_code
                # We might want to construct a company-specific item_code if duplicates exist across companies
                # For now, follow user logic: match by item_code directly
                
                # Check for existing item with same code
                existing_item_name = frappe.db.get_value("Item", {"item_code": item_code}, "name")
                
                # If found by name, but has DIFFERENT QB ID, we have a conflict?
                # User logic says: "if not existing_item: existing_item = ... get value by item_code"
                # If found, it proceeds to update.
            
            try:
                if existing_item_name:
                    doc = frappe.get_doc("Item", existing_item_name)
                    is_new = False
                else:
                    doc = frappe.new_doc("Item")
                    doc.item_code = item_code
                    doc.item_group = "All Item Groups"
                    doc.stock_uom = "Unit"  # User request: "Unit" instead of "Nos"
                    is_new = True
                
                # Standard fields
                doc.quickbooks_item_id = unique_qb_id
                doc.item_name = item_code
                doc.description = fields.get("Description")
                
                # Mandatory for ace_advisory / app logic
                if hasattr(doc, "custom_company"):
                    doc.custom_company = resolved_company
                
                # Rates
                if fields.get("UnitPrice") is not None:
                    doc.standard_rate = flt(fields.get("UnitPrice"))
                
                if fields.get("PurchaseCost") is not None:
                    doc.last_purchase_rate = flt(fields.get("PurchaseCost"))
                    doc.valuation_rate = flt(fields.get("PurchaseCost"))
                
                # Determine if it's a stock item
                # User logic: if TrackQtyOnHand or Type == Inventory -> 1, else 0
                if fields.get("TrackQtyOnHand") or fields.get("Type") == "Inventory":
                    doc.is_stock_item = 1
                else:
                    doc.is_stock_item = 0
                
                # Set inventory start date if provided by QuickBooks
                if fields.get("InvStartDate"):
                    doc.inventory_start_date = fields.get("InvStartDate")
                    
                # Map Accounts (Income/Expense)
                income_account = None
                income_ref = fields.get("IncomeAccountRef", {})
                if income_ref:
                    # Try by QB ID first
                    inc_qb_id = income_ref.get("value")
                    if inc_qb_id:
                        # Use unique ID lookup if account sync adds prefixes, but accounts often sync without prefixes...
                        # Current sync_account.py uses quickbooks_account_id without prefix usually or stores original ID?
                        # Let's try exact match on ID first
                        income_account = frappe.db.get_value("Account", {"quickbooks_account_id": inc_qb_id, "company": resolved_company}, "name")
                    
                    # Fallback to name
                    if not income_account and income_ref.get("name"):
                        income_account = frappe.db.get_value("Account", {"account_name": income_ref.get("name"), "company": resolved_company}, "name")

                expense_account = None
                expense_ref = fields.get("ExpenseAccountRef", {})
                if expense_ref:
                    exp_qb_id = expense_ref.get("value")
                    if exp_qb_id:
                        expense_account = frappe.db.get_value("Account", {"quickbooks_account_id": exp_qb_id, "company": resolved_company}, "name")
                    
                    if not expense_account and expense_ref.get("name"):
                        expense_account = frappe.db.get_value("Account", {"account_name": expense_ref.get("name"), "company": resolved_company}, "name")

                asset_account = None
                asset_ref = fields.get("AssetAccountRef", {})
                if asset_ref:
                    asset_qb_id = asset_ref.get("value")
                    if asset_qb_id:
                        asset_account = frappe.db.get_value("Account", {"quickbooks_account_id": asset_qb_id, "company": resolved_company}, "name")
                    
                    if not asset_account and asset_ref.get("name"):
                        asset_account = frappe.db.get_value("Account", {"account_name": asset_ref.get("name"), "company": resolved_company}, "name")

                # Update Item Defaults
                stores_warehouse = get_company_stores_warehouse(resolved_company)
                
                if income_account or expense_account or asset_account or stores_warehouse:
                    found = False
                    for d in doc.get("item_defaults"):
                        if d.company == resolved_company:
                            if income_account: d.income_account = income_account
                            if expense_account: d.expense_account = expense_account
                            if asset_account: d.custom_default_asset_account = asset_account
                            if stores_warehouse: d.default_warehouse = stores_warehouse
                            found = True
                            break
                    if not found:
                        doc.append("item_defaults", {
                            "company": resolved_company,
                            "income_account": income_account,
                            "expense_account": expense_account,
                            "custom_default_asset_account": asset_account,
                            "default_warehouse": stores_warehouse
                        })

                doc.save(ignore_permissions=True)
                # Commit is handled by caller (sync loop) or we can commit per item if we want safety
                # User logic had commit() at end of single item update.
                # Batch create handles commits, but here we are doing DB ops directly.
                # We should probably commit periodically or let the main loop handle it. 
                # syn_products.py main loop doesn't have a commit?
                # Usually better to commit periodically.
                if (stats["created"] + stats["updated"]) % 10 == 0:
                    frappe.db.commit()
                
                if is_new:
                    stats["created"] += 1
                    tracker.log_success("QuickBooks", tracker_id, "CREATED")
                    quickbooks_item_list.append(str(qb_id)) # Store original ID for tracking map
                else:
                    stats["updated"] += 1
                    tracker.log_success("QuickBooks", tracker_id, "UPDATED")
                    quickbooks_item_list.append(str(qb_id))

            except Exception as e:
                stats["failed"] += 1
                tracker.log_error("QuickBooks", tracker_id, str(e))
                _dbg("create_Item:error", {"item": item_code, "error": str(e)})

        # Final commit
        frappe.db.commit()
        tracker.cleanup()
        
    except Exception as e:
        _dbg("create_Item:global_error", {"error": str(e)})
        # Rethrow or handle?
        raise e

    _dbg("create_Item:end", stats)
    return quickbooks_item_list
def sync_erp_items():
    response_from_quickbooks = sync_erp_items_to_quickbooks()
    if response_from_quickbooks:
        try:
    
            for response_obj in response_from_quickbooks.successes:
                if response_obj:
                    # Get company from item to create unique QB ID
                    # Items don't have company field directly, try to get from default company
                    item_company = (
                        frappe.defaults.get_user_default("company")
                        or frappe.db.get_single_value("Global Defaults", "default_company")
                        or frappe.db.get_value("Company", {}, "name")
                    )
                    if item_company:
                        unique_qb_id = make_unique_qb_id(str(response_obj.Id), item_company)
                        frappe.db.sql(
                            """UPDATE tabItem SET quickbooks_item_id = %s WHERE item_code = %s""",
                            (unique_qb_id, response_obj.Name),
                        )
                    else:
                        # Fallback if company not found
                        frappe.db.sql(
                            """UPDATE tabItem SET quickbooks_item_id = %s WHERE item_code = %s""",
                            (str(response_obj.Id), response_obj.Name),
                        )
                else:
                    raise _("Does not get any response from quickbooks")
        except Exception as e:
            qb_log_exception(
                method="sync_erp_items",
                err=e,
                request_data=locals().get("response_obj"),
                module="sync_products",
            )


def sync_erp_items_to_quickbooks():
    Item_list = []
    for erp_item in erp_item_data():
        try:
            if erp_item:
                create_erp_item_to_quickbooks(erp_item, Item_list)
            else:
                raise _("Item does not exist in ERPNext")
        except Exception as e:
            qb_log_exception(
                method="sync_erp_items_to_quickbooks",
                err=e,
                request_data=erp_item,
                module="sync_products",
            )
    results = batch_create(Item_list)
    return results


def erp_item_data():
    """Get ERPNext items that haven't been synced to QuickBooks yet"""
    erp_item = frappe.db.sql(
        """select item_code, item_name, is_stock_item, Description,
                  income_account, expense_account, asset_account
           from `tabItem`
           where `quickbooks_item_id` is NULL""",
        as_dict=1,
    )
    return erp_item


def create_erp_item_to_quickbooks(erp_item, Item_list):
    item_obj = Item()
    item_obj.Name = erp_item.item_code
    item_obj.FullyQualifiedName = erp_item.item_code
    item_obj.Description = (
        erp_item.Description if erp_item.Description else erp_item.item_name
    )
    item_type_and_Inventory_start_date(item_obj, erp_item)
    item_obj.AssetAccountRef = asset_account_ref(erp_item)
    item_obj.ExpenseAccountRef = expense_account_ref(erp_item)
    item_obj.IncomeAccountRef = income_account_ref(erp_item)
    item_obj.save()
    Item_list.append(item_obj)
    return Item_list


def item_type_and_Inventory_start_date(item_obj, erp_item):
    if erp_item.is_stock_item is True:
        item_obj.Type = "Inventory"
        item_obj.TrackQtyOnHand = True
        item_obj.QtyOnHand = 0
        item_obj.InvStartDate = nowdate()
    else:
        item_obj.Type = "NonInventory"


def income_account_ref(erp_item):
    """Get income account reference for QuickBooks Item from ERPNext Item"""
    # Try to get income account from ERPNext Item if it exists
    income_account_name = erp_item.get("income_account")
    return income_account(erp_item, income_account_name)


def income_account(erp_item, account_name):
    """Map ERPNext income account to QuickBooks account reference"""
    if account_name:
        # Try to find QuickBooks account ID for this ERPNext account
        quickbooks_account_id = frappe.db.get_value(
            "Account", {"name": account_name}, "quickbooks_account_id"
        )
        if quickbooks_account_id:
            return {"value": quickbooks_account_id, "name": account_name}

    # No account mapped - return None to let QuickBooks use defaults
    # Instead of hardcoded values, return None so QuickBooks handles it
    _dbg(
        "income_account:no_mapping",
        {
            "item": erp_item.get("item_code"),
            "message": "No income account mapped, QuickBooks will use default",
        },
    )
    return None


def expense_account_ref(erp_item):
    """Get expense account reference for QuickBooks Item from ERPNext Item"""
    # Try to get expense account from ERPNext Item if it exists
    expense_account_name = erp_item.get("expense_account")
    return expense_account(erp_item, expense_account_name)


def expense_account(erp_item, account_name):
    """Map ERPNext expense account to QuickBooks account reference"""
    if account_name:
        # Try to find QuickBooks account ID for this ERPNext account
        quickbooks_account_id = frappe.db.get_value(
            "Account", {"name": account_name}, "quickbooks_account_id"
        )
        if quickbooks_account_id:
            return {"value": quickbooks_account_id, "name": account_name}

    # No account mapped - return None to let QuickBooks use defaults
    # Instead of hardcoded values, return None so QuickBooks handles it
    _dbg(
        "expense_account:no_mapping",
        {
            "item": erp_item.get("item_code"),
            "message": "No expense account mapped, QuickBooks will use default",
        },
    )
    return None


def asset_account_ref(erp_item):
    """Get asset account reference for QuickBooks Item from ERPNext Item"""
    return asset_account(erp_item)


def asset_account(erp_item):
    """Map ERPNext asset account to QuickBooks account reference"""
    if erp_item.is_stock_item is True:
        # Try to get asset account from ERPNext Item if it exists
        asset_account_name = erp_item.get("asset_account")
        if asset_account_name:
            # Try to find QuickBooks account ID for this ERPNext account
            quickbooks_account_id = frappe.db.get_value(
                "Account", {"name": asset_account_name}, "quickbooks_account_id"
            )
            if quickbooks_account_id:
                return {"value": quickbooks_account_id, "name": asset_account_name}

        # No account mapped - return None to let QuickBooks use defaults
        # Instead of hardcoded values, return None so QuickBooks handles it
        _dbg(
            "asset_account:no_mapping",
            {
                "item": erp_item.get("item_code"),
                "message": "No asset account mapped, QuickBooks will use default",
            },
        )
    return None


def create_opening_stock_entry(
    item_code, qty, posting_date, warehouse, company, valuation_rate=None
):
    """Create a Stock Entry for opening stock"""
    try:
        # Determine valuation rate if not provided
        if valuation_rate is None or valuation_rate == 0:
            # Try to get last purchase rate or standard rate from item
            try:
                item_rates = frappe.db.get_value(
                    "Item",
                    item_code,
                    ["last_purchase_rate", "standard_rate"],
                    as_dict=True
                )
                valuation_rate = item_rates.get("last_purchase_rate") or item_rates.get("standard_rate") or 0
            except:
                valuation_rate = 0

        se = frappe.new_doc("Stock Entry")
        se.purpose = "Material Receipt"
        se.company = company
        se.posting_date = posting_date or nowdate()
        se.remarks = "Opening Stock from QuickBooks Sync"

        row = se.append("items", {})
        row.item_code = item_code
        row.qty = qty
        row.t_warehouse = warehouse
        # Always set basic_rate (valuation rate) - required for stock entries
        row.basic_rate = flt(valuation_rate)

        se.insert()
        se.submit()
        _dbg("create_opening_stock_entry:success", {"item": item_code, "qty": qty})
    except Exception as e:
        _dbg("create_opening_stock_entry:error", {"item": item_code, "error": str(e)})
        # Log but don't fail the sync
        qb_log_exception(
            method="create_opening_stock_entry",
            err=e,
            request_data={"item": item_code, "qty": qty},
            module="sync_products",
        )


def update_item_price(item_code, rate, buying=True, company=None, currency=None):
    """Create or update Item Price"""
    try:
        # Normalize item_code (trim and normalize whitespace)
        item_code = item_code.strip() if item_code else item_code
        
        # Validate that the item exists
        actual_item_code = item_code
        
        # Try multiple lookup strategies
        if not frappe.db.exists("Item", {"item_code": item_code}):
            # Strategy 1: Try without company abbreviation (split on " - ")
            if " - " in item_code:
                base_code = item_code.rsplit(" - ", 1)[0].strip()
                if frappe.db.exists("Item", {"item_code": base_code}):
                    actual_item_code = base_code
                    _dbg(
                        "update_item_price:item_code_adjusted",
                        {
                            "original": item_code,
                            "adjusted": actual_item_code,
                            "strategy": "removed_company_abbr",
                        },
                    )
                else:
                    # Strategy 2: Try with trimmed spaces in the base code
                    base_code_trimmed = " ".join(base_code.split())
                    if (
                        base_code_trimmed != base_code
                        and frappe.db.exists("Item", {"item_code": base_code_trimmed})
                    ):
                        actual_item_code = base_code_trimmed
                        _dbg(
                            "update_item_price:item_code_adjusted",
                            {
                                "original": item_code,
                                "adjusted": actual_item_code,
                                "strategy": "trimmed_spaces",
                            },
                        )
                    else:
                        # Strategy 3: Try to find items starting with base code
                        # (in case there's a slight variation)
                        similar_items = frappe.db.sql(
                            """
                            SELECT item_code
                            FROM `tabItem`
                            WHERE item_code LIKE %s
                            LIMIT 5
                            """,
                            (f"{base_code}%",),
                            as_list=True,
                        )
                        if similar_items:
                            # Log similar items found for debugging
                            similar_codes = [row[0] for row in similar_items]
                            _dbg(
                                "update_item_price:item_not_found_similar",
                                {
                                    "item": item_code,
                                    "base_code": base_code,
                                    "similar_items": similar_codes,
                                    "error": f"Item {item_code} not found. Similar items: {', '.join(similar_codes)}",
                                },
                            )
                        else:
                            _dbg(
                                "update_item_price:item_not_found",
                                {
                                    "item": item_code,
                                    "base_code": base_code,
                                    "error": f"Item {item_code} not found (tried: {base_code})",
                                },
                            )
                        return
            else:
                # Strategy 4: Try with trimmed spaces
                trimmed_code = " ".join(item_code.split())
                if trimmed_code != item_code and frappe.db.exists("Item", {"item_code": trimmed_code}):
                    actual_item_code = trimmed_code
                    _dbg(
                        "update_item_price:item_code_adjusted",
                        {
                            "original": item_code,
                            "adjusted": actual_item_code,
                            "strategy": "trimmed_spaces",
                        },
                    )
                else:
                    # Item doesn't exist - log error and return
                    _dbg(
                        "update_item_price:item_not_found",
                        {
                            "item": item_code,
                            "error": f"Item {item_code} not found",
                        },
                    )
                    return

        if not currency:
            if company:
                currency = frappe.db.get_value("Company", company, "default_currency")

            if not currency:
                currency = (
                    frappe.db.get_value(
                        "Company",
                        frappe.defaults.get_user_default("Company"),
                        "default_currency",
                    )
                    or "USD"
                )

        price_list_name = "Standard Buying" if buying else "Standard Selling"

        # Check if Price List exists, if not find any buying/selling price list
        if not frappe.db.exists("Price List", price_list_name):
            price_list_name = frappe.db.get_value(
                "Price List", {"buying" if buying else "selling": 1}, "name"
            )
            if not price_list_name:
                _dbg("update_item_price:no_price_list_found", {"buying": buying})
                return

        # Final validation - ensure item still exists before creating Item Price
        if not frappe.db.exists("Item", {"item_code": actual_item_code}):
            _dbg(
                "update_item_price:item_not_found_final",
                {
                    "item": item_code,
                    "actual_item_code": actual_item_code,
                    "error": f"Item {actual_item_code} does not exist (final check before creating Item Price)",
                },
            )
            return

        item_price_name = frappe.db.get_value(
            "Item Price",
            {"item_code": actual_item_code, "price_list": price_list_name},
            "name",
        )

        if item_price_name:
            item_price = frappe.get_doc("Item Price", item_price_name)
            if item_price.price_list_rate != rate:
                item_price.price_list_rate = rate
                item_price.save()
                _dbg(
                    "update_item_price:updated",
                    {"item": actual_item_code, "rate": rate},
                )
        else:
            # Double-check item exists before creating new Item Price
            if not frappe.db.exists("Item", {"item_code": actual_item_code}):
                _dbg(
                    "update_item_price:item_not_found_before_create",
                    {
                        "item": actual_item_code,
                        "error": f"Item {actual_item_code} does not exist - cannot create Item Price",
                    },
                )
                return
            
            item_price = frappe.new_doc("Item Price")
            item_price.item_code = actual_item_code
            item_price.price_list = price_list_name
            item_price.price_list_rate = rate
            item_price.currency = currency
            item_price.save()
            _dbg(
                "update_item_price:created",
                {"item": actual_item_code, "rate": rate},
            )

    except Exception as e:
        _dbg("update_item_price:error", {"item": item_code, "error": str(e)})

