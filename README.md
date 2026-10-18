# QuickBooks Master Sync for ERPNext

Move QuickBooks Online master data and opening balances into ERPNext, then compare customer and supplier balances before go-live. Free and MIT licensed, with release branches for ERPNext v15 and v16.

[**Install from Frappe Cloud Marketplace**](https://cloud.frappe.io/marketplace/apps/quickbooks_master_sync) · [Documentation](https://invento-software-limited.github.io/quickbooks_master_sync/) · [Plan your migration](https://invento.com.bd/quickbooks-to-erpnext-migration/?utm_source=github&utm_medium=referral&utm_campaign=quickbooks_master_sync&utm_content=readme_guide)

![QuickBooks Balance Comparison showing matching supplier balances alongside account-category differences in sandbox data](docs/images/balance-comparison.webp)

*Actual product screen using QuickBooks sandbox data. The supplier rows shown match; differences remain in the account summary above. A comparison helps identify work to review, rather than guaranteeing a completed migration.*

## Connect, move, verify

1. **Connect** your QuickBooks Online company through Intuit OAuth 2.0.
2. **Move** master records and opening balances into ERPNext through a manually started background job.
3. **Verify** customer and supplier balances, review logs and resolve differences before go-live.

Live progress reports the current run. The public app does not enable scheduled synchronization or push ERPNext changes back to QuickBooks. QuickBooks Desktop company files require a different migration route.

## What moves into ERPNext

| QuickBooks Online | ERPNext destination |
| --- | --- |
| Chart of accounts | Accounts, including hierarchy and account classification |
| Customers | Customers and related contact/address records |
| Vendors | Suppliers and related contact/address records |
| Items and services | Items |
| Tax codes and rates | Sales Tax Templates |
| Payment terms | Payment Terms Templates |
| Payment methods | Modes of Payment |
| Opening balances | Opening entries; review the resulting ledger and party balances |

Company setup also supports related records such as classes and tax agencies. Review mappings and target-company settings in a test site before using live books.

## Free app and full migration scope

| Capability | Free Marketplace app | Full migration engagement |
| --- | --- | --- |
| Master records and opening balances | Included | Scope agreed with Invento |
| Customer and supplier balance comparison | Included | Scope agreed with Invento |
| Detailed account comparison tabs | Restricted in the public app | Contact Invento |
| Historical invoices, bills, payments and journals | Not included | Confirm coverage with Invento before purchase |

The public repository supplies the free app. Detailed account tabs display an upgrade prompt. Historical transaction migration is a separate scope: ask Invento to confirm supported records, versions, pricing and delivery before committing.

## Evidence from an implementation

Invento's [ACE Advisory case study](https://invento.com.bd/work/ace-advisory-erpnext-vat-software-bangladesh/) describes moving the books of more than 100 client companies into ERPNext. That was a client implementation using the connector behind this app; it is not evidence that the free edition includes every feature delivered in that project.

## Installation

### Frappe Cloud

Start with the [Marketplace listing](https://cloud.frappe.io/marketplace/apps/quickbooks_master_sync) and Frappe Cloud's [installation guide](https://docs.frappe.io/cloud/installing-an-app). App availability depends on your site's version and bench setup.

### Self-hosted ERPNext

Use an existing Frappe/ERPNext v16 bench and its matching release branch:

```bash
cd /path/to/frappe-bench
bench get-app https://github.com/invento-software-limited/quickbooks_master_sync.git --branch version-16
bench --site your-site.example install-app quickbooks_master_sync
bench --site your-site.example migrate
```

Use an existing Frappe/ERPNext v15 bench and its matching release branch:

```bash
cd /path/to/frappe-bench
bench get-app https://github.com/invento-software-limited/quickbooks_master_sync.git --branch version-15
bench --site your-site.example install-app quickbooks_master_sync
bench --site your-site.example migrate
```

This is the development branch. Use a versioned branch for a release installation.

| Branch | Frappe / ERPNext |
| --- | --- |
| version-15 | v15 |
| version-16 | v16 |
| develop | Development; not the release installation target |

## Configure and run

1. Open **Quickbooks Settings** and enable QuickBooks Online and the QuickBooks-to-ERPNext direction.
2. Enter your Intuit app's Client ID and Client Secret in **Consumer key** and **Consumer Secret**. Match Sandbox Mode to the Intuit environment.
3. Register the callback URI from the [user guide](docs/user_guide.md#connect-quickbooks-online) and authorize the connection. Connecting can create/update company-related records, so begin on a test site.
4. Review Company Settings, tax mappings and the connected company before starting a run.
5. Start the sync from the settings form and follow progress. Menu wording varies by release; the direction is QuickBooks Online to ERPNext.
6. Open **QuickBooks Balance Comparison**, compare customers and suppliers, and review import/error logs before go-live.

See the [full user guide](docs/user_guide.md) for the callback, setup checks, troubleshooting and rerun considerations.

## Limits and support

- Opening balance routines can create and submit accounting documents. The normal run uses the current date when no cutoff is supplied; agree the cutover date with your finance team and test the result.
- A completed progress indicator is not financial sign-off. Some steps log errors and continue, so review logs and balances separately.
- Existing-record checks are implemented in individual routines. There is no blanket promise of a duplicate-free rerun or one-click rollback. Restore a test backup when validating a fresh migration.
- Company-specific configuration is supported. The current Quickbooks Settings connection is shared; this is not a promise of independent OAuth credentials for every company at once.
- Share version numbers, steps to reproduce and redacted errors in [GitHub Issues](https://github.com/invento-software-limited/quickbooks_master_sync/issues). Keep credentials, raw API responses and client books out of public issues.

![QuickBooks Debug Viewer listing stored API responses by data type and company](docs/images/debug-viewer.webp)

*Sandbox data. Debug records help investigate imports; they may contain financial or personal information on a real site.*

## Choose your next step

[**Install the free app**](https://cloud.frappe.io/marketplace/apps/quickbooks_master_sync) · [**Discuss your migration**](https://invento.com.bd/free-consultation/?utm_source=github&utm_medium=referral&utm_campaign=quickbooks_master_sync&utm_content=readme_enquiry) · [Read the migration guide](https://invento.com.bd/quickbooks-to-erpnext-migration/?utm_source=github&utm_medium=referral&utm_campaign=quickbooks_master_sync&utm_content=readme_resources)

## Contributing and licence

Use a development site and install the repository's pre-commit hooks before proposing changes:

```bash
cd apps/quickbooks_master_sync
pre-commit install
```

See [.pre-commit-config.yaml](.pre-commit-config.yaml) for the configured checks. MIT licensed; see [license.txt](license.txt). Built and maintained by [Invento Software Limited](https://invento.com.bd/).
