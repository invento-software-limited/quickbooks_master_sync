# 📖 QuickBooks Master Sync - User Guide

## 🏁 Getting Started

### Prerequisites
-   ERPNext v16+ instance with administrator access.
-   A QuickBooks Online account with API access.
-   QuickBooks Developer app credentials (Client ID and Client Secret).

### 🚀 Installation

Install the app using the bench CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/invento-software-limited/quickbooks_master_sync --branch version-16
bench --site your-site.com install-app quickbooks_master_sync
```

After installation, run a bench migrate and restart the server:

```bash
bench migrate
bench restart
```

## ⚙️ Configuration

### 1. QuickBooks Developer App Setup

Before configuring in ERPNext, create a QuickBooks Online app:

1.  Go to the [QuickBooks Developer Portal](https://developer.intuit.com/).
2.  Create a new app and select **Accounting** API.
3.  Under **Keys & OAuth**, note your **Client ID** and **Client Secret**.
4.  Set the **Redirect URI** to: `https://your-erpnext-domain.com/api/method/quickbooks_master_sync.api.oauth.callback`
5.  Enable **OpenID Connect** if needed for user verification.
6.  Subscribe to the appropriate QuickBooks API scopes:
    -   `com.intuit.quickbooks.accounting`
    -   `openid` (optional)
    -   `profile` (optional)
    -   `email` (optional)

### 2. QuickBooks Settings in ERPNext

1.  Navigate to **QuickBooks Settings** in the ERPNext workspace.
2.  Enter your **Client ID** and **Client Secret** from the QuickBooks Developer Portal.
3.  Set the **Company** for which you want to sync data.
4.  Choose sync options (which master data types to sync).
5.  Click **Connect QuickBooks** to initiate the OAuth2 flow.
6.  Authorize the connection in the QuickBooks popup window.

### 3. Company Mapping

1.  After OAuth2 authorization, select the QuickBooks company to map.
2.  The system matches QuickBooks company currency and fiscal year with ERPNext.
3.  Sync can proceed once mapping is confirmed.

## 🎯 Running a Sync

### Full Master Data Sync

1.  Go to **QuickBooks Settings** → **Actions**.
2.  Click **Sync QuickBooks**.
3.  A progress dialog appears showing real-time sync status.
4.  Wait for all steps to complete (✅ indicators for each data type).
5.  Review the sync summary for any mismatches or errors.

### Individual Data Sync

You can also trigger sync for specific data types:

-   **Sync Chart of Accounts**: Sync only Accounts.
-   **Sync Customers**: Sync only Customers.
-   **Sync Suppliers**: Sync only Suppliers/Vendors.
-   **Sync Items**: Sync only Items and Services.
-   **Sync Tax Rates**: Sync only Tax Codes.
-   **Sync Payment Terms**: Sync only Payment Terms Templates.
-   **Sync Payment Methods**: Sync only Modes of Payment.

## 🔍 Balance Comparison

### Customer & Supplier Reconciliation

1.  Navigate to **QuickBooks Balance Comparison**.
2.  Select **Customers** or **Suppliers** tab.
3.  View side-by-side balances from QuickBooks and ERPNext.
4.  Summary cards show:
    -   **Matched**: Records with identical balances.
    -   **Mismatched**: Records with differing balances.
    -   **QuickBooks Only**: Records only in QuickBooks.
    -   **ERPNext Only**: Records only in ERPNext.
5.  Click on any record to see detailed breakdown.

### Account Balance Comparison

> 🔒 This feature is available in the premium version. Contact Invento Software Limited to unlock.

### Opening Balances Sync

Opening balances are automatically synced at the end of each master data sync cycle:
1.  After all master data types are synced, the system checks for opening balance data.
2.  Opening balances are mapped from QuickBooks accounts to corresponding ERPNext accounts.
3.  Journal Entries are created in ERPNext to reflect opening positions.

### Debug Log Viewer

1.  Navigate to **QuickBooks Debug Viewer**.
2.  View API request/response logs for troubleshooting.
3.  Use filters by date, data type, or status.
4.  Click **Clear Logs** to remove old entries with an audit trail.

## 🛠️ Troubleshooting

### OAuth2 Connection Issues

| Problem | Solution |
|---|---|
| Authorization popup blocked | Allow popups for your ERPNext domain. |
| Token expired | Re-authorize via **QuickBooks Settings** → **Reconnect**. |
| Invalid redirect URI | Verify the redirect URI in QuickBooks Developer Portal matches exactly. |
| Scope mismatch | Ensure all required scopes are enabled in your QuickBooks app. |

### Sync Errors

| Problem | Solution |
|---|---|
| Account mapping failed | Verify Company currency matches between QuickBooks and ERPNext. |
| Customer not found | Check if the Customer exists in QuickBooks before syncing. |
| Item type mismatch | Review Item type mappings in settings. |
| Rate limit exceeded | Wait a few minutes and retry. QuickBooks API has usage limits. |

### Common Issues

1.  **"QuickBooks company not found"**: Ensure you have selected the correct QuickBooks company during OAuth2.
2.  **"Sync stuck at 0%"**: Check your internet connection and QuickBooks API status.
3.  **"Balance mismatch"**: Verify the date range and account mappings.
4.  **"Duplicate records"**: Enable **Skip Existing** option in sync settings.

## 📋 API Reference

QuickBooks Master Sync exposes the following whitelisted API endpoints:

### OAuth2 Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/method/quickbooks_master_sync.api.oauth.get_authorization_url` | Get QuickBooks OAuth2 URL |
| GET | `/api/method/quickbooks_master_sync.api.oauth.callback` | OAuth2 callback handler |
| POST | `/api/method/quickbooks_master_sync.api.oauth.disconnect` | Disconnect QuickBooks connection |

### Sync Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/method/quickbooks_master_sync.api.sync.start` | Start full master data sync |
| POST | `/api/method/quickbooks_master_sync.api.sync.status` | Get current sync status |
| POST | `/api/method/quickbooks_master_sync.api.sync.stop` | Stop active sync |

### Data Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/method/quickbooks_master_sync.api.data.get_companies` | List QuickBooks companies |
| POST | `/api/method/quickbooks_master_sync.api.data.get_accounts` | Get synced accounts |
| POST | `/api/method/quickbooks_master_sync.api.data.get_customers` | Get synced customers |
| POST | `/api/method/quickbooks_master_sync.api.data.get_suppliers` | Get synced suppliers |
| POST | `/api/method/quickbooks_master_sync.api.data.get_items` | Get synced items |

## ❓ FAQ

### Is this app free?
The master data sync features are free and open-source. Transactional data sync and premium features require a license from Invento Software Limited.

### Does it support multiple companies?
Yes, you can configure separate QuickBooks connections for each ERPNext Company.

### How often should I sync?
We recommend syncing daily or weekly, depending on how frequently master data changes in QuickBooks.

### Can I customize the field mappings?
Field mappings are pre-configured but can be extended through Frappe hooks and custom scripts.

### What happens if a sync fails?
The system provides detailed error logs in the QuickBooks Debug Viewer. Failed items are logged without affecting already-synced data.

### How do I get support?
Contact the Invento Software Limited support team via: munim@invento.com.bd

### Is my data secure?
Yes, all OAuth2 tokens are encrypted at rest. Data transfer uses HTTPS. No data is stored outside your ERPNext instance and QuickBooks Online.

### Can I use this with Frappe Cloud?
Yes, the app is fully compatible with Frappe Cloud and Docker-based deployments.
