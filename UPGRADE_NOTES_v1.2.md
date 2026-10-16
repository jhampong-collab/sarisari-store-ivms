# Sari-Sari Store v1.2 — safe upgrade notes

## Features
- Prominent in-app low-stock warning on Products, using existing reorder levels.
- Today's sales revenue, transaction count, and gross profit from **cash sales with known purchase costs**.
- Optional purchase cost when creating a product; Owner/Admin can set cost on existing products. New cash sales store a snapshot of purchase cost so later cost edits don't change their historical margin.
- Products, sales, and customer credit CSV downloads from Reports (Owner/Admin). CSV opens in Excel. Amounts are in **centavos**, indicated by `_cents` headers.

## Profit limitations
- Historical cash sales do not have stored costs, so profit is explicitly marked incomplete when applicable. Updating a product's cost today **does not backfill** past sales.
- The profit metric is gross profit from cash sales only (sales less recorded unit purchase cost); it does not include credit charges, operating expenses, spoilage, tax, or other business costs.
- No mobile push notifications are added.

## Before deployment
1. Export/backup your Supabase database, including schema and data, and store a copy outside Render.
2. Verify the Reports SQL fix is already working on your live version.
3. Prefer testing with a separate Supabase test project and Render staging service. Do not point staging at production database.
4. Upload the contents of this folder to your GitHub repo (preserve Render DATABASE_URL and other secrets; never commit them).
5. The app performs additive migrations on startup: `products.cost_cents` and `sales.unit_cost_cents` (nullable). No tables or rows are dropped.
6. Test logins/roles, existing sales, credit, reports, exports, and low stock before allowing normal store use.
7. Verify on mobile that the PWA prompts to update to v1.2.0.

## Rollback
Restore previous code if necessary. Added columns are harmless when running previous code. Do not drop columns to roll back. If a migration fails, review Render logs and repair in staging before retrying.

## Tests
Python and JavaScript syntax were checked. Full automated Flask tests and live PostgreSQL integration tests were not run in this environment.
