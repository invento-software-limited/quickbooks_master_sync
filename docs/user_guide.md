# QuickBooks Master Sync user guide

This guide covers the free app for ERPNext v15 and v16: connection, master records, opening balances and customer/supplier comparison. Historical transaction migration and detailed account tabs require a separate discussion with Invento.

## Before you begin

Use a test site and take a restorable database/files backup. Agree the target company, currency, fiscal year, account mapping and cutover date with the finance owner. The connection process can create/update company-related records before you start the main run.

The opening-balance routine defaults to the current date when no cutoff is supplied, and the standard background run requests automatic submission. Do not assume the screen provides a historical cutoff selector or a reversible preview.

## Installation

On Frappe Cloud, follow the [Marketplace listing](https://cloud.frappe.io/marketplace/apps/quickbooks_master_sync) and [Frappe Cloud installation guide](https://docs.frappe.io/cloud/installing-an-app).

For an existing self-hosted bench, use the matching release:

### ERPNext v16

```bash
cd /path/to/frappe-bench
bench get-app https://github.com/invento-software-limited/quickbooks_master_sync.git --branch version-16
bench --site your-site.example install-app quickbooks_master_sync
bench --site your-site.example migrate
```

### ERPNext v15

```bash
cd /path/to/frappe-bench
bench get-app https://github.com/invento-software-limited/quickbooks_master_sync.git --branch version-15
bench --site your-site.example install-app quickbooks_master_sync
bench --site your-site.example migrate
```

Keep Frappe and ERPNext on matching major versions. The develop branch is for development.

## Connect QuickBooks Online

1. In the Intuit Developer Portal, create/select your QuickBooks Online app and its Accounting API access. Use sandbox credentials for a sandbox company and production credentials for a live company.
2. Register this exact HTTPS redirect URI, replacing only the site hostname:

```text
https://your-site.example/api/method/quickbooks_master_sync.quickbooks_master_sync.doctype.quickbooks_settings.quickbooks_settings.First_callback
```

3. In ERPNext, open **Quickbooks Settings**. Enable **Enable Quickbooks Online** and **Quickbooks to ERPNext**.
4. Enter the Intuit Client ID in **Consumer key**, and Client Secret in **Consumer Secret**. Set **Sandbox Mode** for the environment being connected.
5. Save, use **Connect to Quickbooks**, and authorize the intended company in Intuit's popup. Allow popups only for your trusted ERPNext site when needed.
6. Review the company created/updated by the connection, Company Settings rows and tax mappings. Check the active company before every run.

The code stores the active connection in the Quickbooks Settings singleton. Company Settings rows contain company-specific configuration; they are not independent OAuth credential stores. Reconnect deliberately when changing the connected QuickBooks company.

## Run and inspect a sync

Start the sync action from Quickbooks Settings. In the current interface an Actions item can be labelled **Sync Data to Quickbooks**; despite that wording, the enabled direction in this public app is **QuickBooks Online to ERPNext**. The form also provides connection and comparison actions. Verify your release's screen before proceeding.

The app queues a background job and publishes progress during the run. Company setup imports accounting foundations, then the main run handles masters and opening balances. The code may log an individual step failure and continue, so the final progress message does not replace a log review.

1. Check the connected company and environment.
2. Watch progress and inspect Quickbooks Log for errors or skipped records.
3. Review imported master records and opening documents in ERPNext.
4. Open **QuickBooks Balance Comparison** and compare **Customers** and **Suppliers** for the intended company/date.
5. Investigate missing records and differences before financial sign-off.

## Reconciliation and debug records

The free app compares customer and supplier balances. Account summary totals are available, but detailed matched/mismatched account tabs show a premium prompt. Do not mistake account summary totals for unlocked detailed account comparison.

The **QuickBooks Debug Viewer** lists stored QuickBooks response files for investigation, including company and data-type context. Its item-record reconciliation checks imported records; it is not a substitute for comparing the general ledger, receivables and payables.

![QuickBooks Debug Viewer with stored sandbox response files](images/debug-viewer.webp)

Debug responses can contain confidential information. Keep them inside the authorized environment; redact evidence before sharing a public issue. Do not clear evidence needed to investigate a failed migration.

## Reruns and recovery

Several routines match records by QuickBooks IDs or check whether opening entries already exist. This does not guarantee that every partial run can be repeated without changes. Review which documents were created/submitted, resolve the underlying error and validate a rerun on the test site.

There is no documented one-click rollback for a whole migration. **Delete Company Data** is a separate destructive utility, not an undo button. Use a restorable test backup when you need a clean starting point, and have the finance owner review any accounting corrections.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Authorization popup does not open | Browser popup permission for the ERPNext domain; saved credentials and enabled connector |
| Invalid redirect URI | Exact HTTPS callback above, including capitalization, and the Intuit app/environment |
| Wrong QuickBooks company | Realm/company selected during authorization; reconnect using the connection action before running again |
| Background run does not advance | Worker/queue health, realtime connectivity, ERPNext error logs and Quickbooks Log |
| Records are missing despite a completed indicator | Individual step errors and skipped records; some errors are caught so later steps can continue |
| Balance difference | Selected company/date, account and party mapping, source data and submitted opening documents |
| Existing entry is skipped | Check its QuickBooks ID and document state before attempting a rerun; do not delete it merely to bypass the check |
| Detailed account tab is restricted | This is outside the free app; discuss the required scope with Invento |

## Integration reference

The following existing whitelisted method paths are verified in the public source. They are for maintainers integrating with Frappe's authenticated method API, not an unauthenticated public service. No API was added by this documentation update.

| Method path | Purpose |
| --- | --- |
| quickbooks_master_sync.quickbooks_master_sync.api.sync_quickbooks_resources | Queue a configured master/opening-balance run |
| quickbooks_master_sync.quickbooks_master_sync.api.compare_party_balances | Compare Customer or Supplier balances using party_type, company_name, as_of_date and tolerance |
| quickbooks_master_sync.quickbooks_master_sync.api.get_quickbooks_debug_files | List stored response files for investigation |
| quickbooks_master_sync.quickbooks_master_sync.doctype.quickbooks_settings.quickbooks_settings.First_callback | Intuit OAuth callback; use the exact registered redirect URI |

See the [source](https://github.com/invento-software-limited/quickbooks_master_sync) for signatures and access handling on your installed branch. Older documentation paths under api.oauth, api.sync and api.data do not match the current package structure.

## Common questions

### Is this continuous two-way sync?
No. The documented workflow is a manually started pull from QuickBooks Online. Live progress describes the running job. No scheduled sync is enabled in the app hooks.

### Does it import QuickBooks Desktop files?
No. It connects to QuickBooks Online through Intuit's API.

### Are old invoices and payments included?
Not in the free app's master-data run. Confirm transaction types, date coverage, compatibility and pricing with Invento when scoping a full migration.

### Does free mean no project cost?
The app is MIT licensed. Hosting, migration preparation, implementation and support can have separate costs.

### How do I get help?
Use [GitHub Issues](https://github.com/invento-software-limited/quickbooks_master_sync/issues) for redacted reproducible problems. Include Frappe/ERPNext/app versions, company/environment context and steps taken. For project help, [discuss your migration](https://invento.com.bd/free-consultation/?utm_source=github_docs&utm_medium=referral&utm_campaign=quickbooks_master_sync&utm_content=guide_enquiry) or contact [munim@invento.com.bd](mailto:munim@invento.com.bd).
