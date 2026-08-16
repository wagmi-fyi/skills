# PDF Ingestion

Principles for extracting financial data from PDF statements. Load this reference before building a PDF adapter or ingesting PDF-sourced data for the first time in a session.

---

## 1. Stage and Verify Before Committing

Never ingest PDF-extracted data directly into the database. Extract to an intermediate format first (CSV or JSON), verify it against the source document's control totals, then ingest.

The verification step catches sign errors, missing rows, and column misreads before they touch the DB.

**Workflow:**
```
PDF → Extract → Intermediate file → Verify against statement → Ingest
```

**Verify means:** running balance matches closing balance on every statement ingested. If any check fails, fix the extraction — do not ingest and fix later.

---

## 2. Respect the Document's Natural Unit of Work

A bank or credit card statement is a self-contained, self-validating unit. It has an opening balance, a closing balance, and category totals that must reconcile. Extract and verify one statement at a time.

---

## 3. Spatial Layout Is Semantic Data

In a multi-column PDF (credits vs. charges, debits vs. deposits), the column a number occupies IS the data type. Text-only extraction that reads left-to-right destroys this information.

Use a layout-aware PDF library (e.g., pdfplumber) and classify values by their x-coordinate position on the page. Map column positions to semantic roles (date, description, charges, credits, payments) and preserve that mapping in the extraction logic.

**Validate the mapping:** Check extracted category totals against the statement's Account Summary. For example, if charges and credits don't match, the column classification is wrong — fix the extraction, don't adjust the numbers.
