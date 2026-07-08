# 📘 QuickBooks Master Sync - Product Overview

## 👋 Introduction
Welcome to **QuickBooks Master Sync** for Frappe/ERPNext — built and maintained by **Invento Software Limited**.

Bridging the gap between **QuickBooks Online** and **ERPNext** has never been more seamless. This application provides reliable, real-time synchronization of master data — helping businesses maintain consistency across their accounting and ERP systems without manual data entry or error-prone exports.

> ⚠️ **Note:** This app synchronizes **master data only** (Accounts, Customers, Suppliers, Items, Tax Rates, Payment Terms, etc.). Transactional data sync (Invoices, Payments, etc.) is available in the **full version**. Contact our support team to upgrade.

## 👥 Target Audience
This application is designed for businesses that use both **QuickBooks Online** for accounting and **ERPNext** for ERP operations.

### Business Types
-   **SMEs**: Small to medium enterprises running dual systems who need data consistency.
-   **Accounting Firms**: Manage client data across QuickBooks and ERPNext.
-   **ERP Consultants**: Simplify data migration and ongoing synchronization for clients.
-   **Enterprises**: Large organizations migrating from QuickBooks to ERPNext or maintaining both.

### Key Users
-   **Accountants**: To ensure Chart of Accounts, Customers, and Suppliers are in sync.
-   **IT Administrators**: To configure OAuth2 connections and monitor sync health.
-   **Operations Managers**: To keep Items, Tax Rates, and Payment Terms aligned across systems.

## 🛠️ Features & Capabilities

### ✅ What's Included

#### 🔗 OAuth2 Authentication
-   Secure QuickBooks Online OAuth2 flow built into the ERPNext interface.
-   Access token and refresh token management handled automatically.
-   Multi-company support with per-company QuickBooks credentials.

#### 🏢 Company Sync
-   Sync company-level configuration from QuickBooks.
-   Map QuickBooks company data to ERPNext company structure.
-   Automatic currency and accounting period alignment.

#### 📒 Chart of Accounts
-   Full synchronization of the QuickBooks Chart of Accounts to ERPNext Accounts.
-   Preserves account types, root types, and account hierarchies.
-   Smart mapping between QuickBooks account classifications and ERPNext root types (Asset, Liability, Equity, Income, Expense).

#### 👥 Customers & Suppliers
-   Sync QuickBooks Customers → ERPNext Customers.
-   Sync QuickBooks Vendors → ERPNext Suppliers.
-   Includes contact details, billing addresses, and tax registrations.

#### 📦 Items
-   Sync QuickBooks Items/Services → ERPNext Items.
-   Maps product and service types appropriately.

#### 💰 Tax Rates
-   Sync QuickBooks Tax Codes and Tax Rates → ERPNext Sales Tax Templates.
-   Supports complex tax groups.

#### 📅 Payment Terms
-   Sync QuickBooks payment terms → ERPNext Payment Terms Templates.

#### 💳 Payment Methods
-   Sync QuickBooks payment methods → ERPNext Mode of Payment.

#### 📊 Balance Comparison *(Premium)*
-   Side-by-side comparison of QuickBooks and ERPNext account balances.
-   Summary cards: Matched, Mismatched, QuickBooks Only, ERPNext Only.
-   **Customer & Supplier balance reconciliation** — fully functional.
-   Date-wise comparison drilldown per account.
-   Premium gate on detailed account tabs (contact Invento to unlock).

#### 🔍 Debug Viewer
-   Built-in QuickBooks Debug Log Viewer for inspecting sync activity.
-   View, delete, and manage QuickBooks API response files.
-   Quick log clearing with audit trail.

#### 🏦 Opening Balances Sync
-   Automatically sync opening balances at the end of the master data sync.
-   Ensures ERPNext starts from the same financial position as QuickBooks.

#### 📡 Real-Time Sync Progress
-   Live sync progress dialog with step-by-step status updates.
-   WebSocket-powered via Frappe's `frappe.realtime` system.
-   Shows completed ✅, running 🔄, and failed ❌ steps in real time.
-   Non-dismissible dialog during sync to prevent accidental interruptions.

### ❌ What's Not Included (Free Version)
-   **Transactional Data Sync**: Invoices, Bills, Payments, and Journal Entries are available in the full version.
-   **Detailed Account Balance Tabs**: Comparison drilldown into individual accounts is premium-gated.

## 🔒 Premium Features

| Feature | Free | Full |
|---|---|---|
| Master Data Sync | ✅ | ✅ |
| Opening Balance Sync | ✅ | ✅ |
| Customer/Supplier Balance Comparison | ✅ | ✅ |
| Account Balance Comparison (Matched/Mismatched/Only) | 🔒 | ✅ |
| Transactional Data Sync (Invoices, Bills, Payments) | 🔒 | ✅ |
| Journal Entry Sync | 🔒 | ✅ |

📧 **Contact:** Support Team  
🌐 **Website:** Invento Software Limited

## 🚀 Product Roadmap
We are committed to evolving with the ecosystem. Here is what's coming next:

1.  **Two-Way Sync**: Push ERPNext changes back to QuickBooks for specific data types.
2.  **Scheduled Auto-Sync**: Cron-based automation for hands-free synchronization.
3.  **Advanced Analytics Dashboards**: Real-time sync metrics and discrepancy reports.
4.  **Full Transactional Sync**: Invoice, Bill, Payment, and Journal Entry synchronization.
5.  **Multi-Environment Sync**: Stage and production environment management.
