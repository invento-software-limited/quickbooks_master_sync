<h1 align="center">QuickBooks Master Sync</h1>

<p align="center">
  A Frappe/ERPNext app for seamlessly syncing <strong>master data</strong> from QuickBooks Online into ERPNext — built and maintained by Invento Software Limited.
</p>

---

## 🧭 Overview

**QuickBooks Master Sync** bridges the gap between QuickBooks Online and ERPNext by providing a reliable, real-time synchronization of master data — helping businesses maintain consistency across their accounting and ERP systems.

> ⚠️ **Note:** This app synchronizes **master data only** (Accounts, Customers, Suppliers, Items, Tax Rates, Payment Terms, etc.). Transactional data sync (Invoices, Payments, etc.) is available in the **full version**. Contact our support team to upgrade.

---

## ✨ Features

### 🔗 OAuth2 Authentication
- Secure QuickBooks Online OAuth2 flow built into the ERPNext interface
- Access token and refresh token management handled automatically
- Multi-company support with per-company QuickBooks credentials

### 🏢 Company Sync
- Sync company-level configuration from QuickBooks
- Map QuickBooks company data to ERPNext company structure
- Automatic currency and accounting period alignment

### 📒 Chart of Accounts
- Full synchronization of the QuickBooks Chart of Accounts to ERPNext Accounts
- Preserves account types, root types, and account hierarchies
- Smart mapping between QuickBooks account classifications and ERPNext root types (Asset, Liability, Equity, Income, Expense)

### 👥 Customers & Suppliers
- Sync QuickBooks Customers → ERPNext Customers
- Sync QuickBooks Vendors → ERPNext Suppliers
- Includes contact details, billing addresses, and tax registrations

### 📦 Items
- Sync QuickBooks Items/Services → ERPNext Items
- Maps product and service types appropriately

### 💰 Tax Rates
- Sync QuickBooks Tax Codes and Tax Rates → ERPNext Sales Tax Templates
- Supports complex tax groups

### 📅 Payment Terms
- Sync QuickBooks payment terms → ERPNext Payment Terms Templates

### 💳 Payment Methods
- Sync QuickBooks payment methods → ERPNext Mode of Payment

### 📊 Balance Comparison *(Premium)*
- Side-by-side comparison of QuickBooks and ERPNext account balances
- Summary cards: Matched, Mismatched, QuickBooks Only, ERPNext Only
- **Customer & Supplier balance reconciliation** — fully functional
- Date-wise comparison drilldown per account
- Premium gate on detailed account tabs (contact Invento to unlock)

### 🔍 Debug Viewer
- Built-in QuickBooks Debug Log Viewer for inspecting sync activity
- View, delete, and manage QuickBooks API response files
- Quick log clearing with audit trail

### 🏦 Opening Balances Sync
- Automatically sync opening balances at the end of the master data sync
- Ensures ERPNext starts from the same financial position as QuickBooks

### 📡 Real-Time Sync Progress
- Live sync progress dialog with step-by-step status updates
- WebSocket-powered via Frappe's `frappe.realtime` system
- Shows completed ✅, running 🔄, and failed ❌ steps in real time
- Non-dismissible dialog during sync to prevent accidental interruptions

---

## 🔒 Premium Features

The following features are available in the **full version** of QuickBooks Master Sync. Contact us to unlock:

| Feature | Free | Full |
|---|:---:|:---:|
| Master Data Sync | ✅ | ✅ |
| Opening Balance Sync | ✅ | ✅ |
| Customer/Supplier Balance Comparison | ✅ | ✅ |
| Account Balance Comparison (Matched/Mismatched/Only) | 🔒 | ✅ |
| Transactional Data Sync (Invoices, Bills, Payments) | 🔒 | ✅ |
| Journal Entry Sync | 🔒 | ✅ |

📧 **Contact:** Support Team  
🌐 **Website:** Invento Software Limited

---

## 🚀 Installation

You can install this app using the bench CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app quickbooks_master_sync
```

### Requirements

- Frappe Framework v15+
- ERPNext v15+
- QuickBooks Online account with API access

---

## ⚙️ Configuration

1. Navigate to **QuickBooks Settings** in ERPNext
2. Enter your QuickBooks **Client ID** and **Client Secret**
3. Click **Connect QuickBooks** to complete the OAuth2 authorization
4. Select the **Company** to sync
5. Use **Actions → Sync QuickBooks** to run a full master data sync

---

## 🧪 Development

This app uses `pre-commit` for code formatting and linting. Please install pre-commit and enable it for this repository:

```bash
cd apps/quickbooks_master_sync
pre-commit install
```

Pre-commit is configured to use the following tools:

- `ruff` — Python linting and formatting
- `eslint` — JavaScript linting
- `prettier` — Code formatting
- `pyupgrade` — Python syntax modernization

---

## 🏗️ Architecture

```
quickbooks_master_sync/
├── quickbooks_master_sync/
│   ├── api/                    # Frappe whitelisted API endpoints
│   ├── doctype/
│   │   ├── quickbooks_settings/  # Main settings DocType + UI
│   │   └── quickbooks_log/       # Sync activity log
│   ├── page/
│   │   ├── quickbooks_balance_comparison/  # Balance comparison page
│   │   └── quickbooks_debug_viewer/        # Debug log viewer
│   ├── sync/                   # Sync orchestration modules
│   │   ├── sync_company.py
│   │   ├── sync_opening_balances.py
│   │   └── ...
│   ├── utils/                  # Shared utilities
│   ├── compare_balances.py     # Balance comparison logic
│   └── reconciliation.py       # Reconciliation helpers
├── pyqb/                       # QuickBooks Python SDK (bundled)
└── hooks.py
```

---

## 📄 License

MIT — see LICENSE for details.

---

<p align="center">
  Built with ❤️ by <strong>Invento Software Limited</strong>
</p>
