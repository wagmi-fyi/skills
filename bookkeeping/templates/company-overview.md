# Company Overview: {{client_name}}

<!-- This file provides baseline context about the client's business, financial structure, and data ecosystem. It's loaded at the start of every session and updated as you learn more about the client. -->

---

## Business Model

<!-- Describe what the client does, how they make money, and what kind of business they run. Include industry, major revenue streams, business structure, and any unique aspects of their model. -->

**Industry:** {{industry}}

**Revenue Model:**
{{revenue_model_description}}

**Major Revenue Streams:**
- {{revenue_stream_1}} — {{description_or_percentage}}
- {{revenue_stream_2}} — {{description_or_percentage}}
- {{revenue_stream_3}} — {{description_or_percentage}}

**Business Structure:**
{{sole_proprietor_vs_partnership_vs_multi_entity}}

**Seasonal Patterns:**
{{seasonal_notes_or_none}}

---

## Major Expense Lines

<!-- Top expense categories, typical monthly ranges, seasonal patterns, unusual or large recurring expenses. This helps with coding expectations and anomaly detection. -->

**Top Expense Categories:**
1. {{expense_category_1}} — {{typical_monthly_amount_or_range}}
2. {{expense_category_2}} — {{typical_monthly_amount_or_range}}
3. {{expense_category_3}} — {{typical_monthly_amount_or_range}}
4. {{expense_category_4}} — {{typical_monthly_amount_or_range}}
5. {{expense_category_5}} — {{typical_monthly_amount_or_range}}

**Seasonal or Irregular Expenses:**
{{seasonal_expense_patterns_or_none}}

**Unusual or Large Recurring Expenses:**
{{unusual_expenses_or_none}}

---

## Data Sources

<!-- All systems that feed financial data into the close process. For each: name, type, adapter used, format, connection details, frequency. -->

### Banking
- **{{bank_name_1}}** — {{account_type}} | Adapter: {{adapter_name}} | Format: {{csv_pdf_ofx_etc}} | Notes: {{any_connection_or_timing_notes}}
- **{{bank_name_2}}** — {{account_type}} | Adapter: {{adapter_name}} | Format: {{csv_pdf_ofx_etc}} | Notes: {{any_connection_or_timing_notes}}

### Revenue Sources
- **{{source_name_1}}** (e.g., Stripe, Shopify, Amazon) — Adapter: {{adapter_name}} | Format: {{api_csv_export}} | Notes: {{frequency_or_timing}}
- **{{source_name_2}}** — Adapter: {{adapter_name}} | Format: {{api_csv_export}} | Notes: {{frequency_or_timing}}

### Payroll
- **{{payroll_provider}}** — Adapter: {{adapter_name}} | Format: {{format}} | Notes: {{frequency_and_access_notes}}

### Inventory or Other Systems
- **{{inventory_or_other_system}}** — Adapter: {{adapter_name}} | Format: {{format}} | Notes: {{connection_details}}

**Data Source Notes:**
{{any_quirks_timing_issues_or_dependencies}}

---

## Financial Background

<!-- When the company started, entity type, fiscal year, accounting method. This drives period setup, tax considerations, and compliance requirements. -->

**Entity Type:** {{llc_s_corp_c_corp_sole_prop_partnership}}

**State of Formation:** {{state}}

**Incorporation/Formation Date:** {{date_or_year}}

**Fiscal Year:** {{calendar_year_or_custom_fiscal_year}}
_If custom:_ FY starts {{fiscal_start_month_and_day}}

**Accounting Method:** {{cash_or_accrual}}

**EIN:** {{ein_or_not_applicable}}

**Financial History Notes:**
{{any_prior_accounting_history_conversions_or_transitions}}

---

## Key People & Contacts

<!-- Two sub-sections: Internal (to the firm) and External (client-side). For each person: name, role, what they handle. -->

### Internal (Your Firm)
<!-- If firm context exists with a team roster (check firm content_manifest),
     auto-populate this section during onboarding with assigned team members. -->
- **Engagement Lead:** {{name}} — {{what_they_handle}}
- **Staff Assigned:** {{name}} — {{what_they_handle}}
- **Reviewer:** {{name}} — {{what_they_handle}}

### External (Client-Side)
- **Primary Contact:** {{name}} — {{role}} — {{email_phone}} — {{what_questions_or_decisions_they_handle}}
- **Decision-Maker:** {{name}} — {{role}} — {{email_phone}} — {{what_they_approve_or_sign_off_on}}
- **Additional Contacts:**
  - {{name}} — {{role}} — {{what_they_handle}}
  - {{name}} — {{role}} — {{what_they_handle}}

**Communication Preferences:**
{{preferred_channels_timing_tone}}

---

## Accounting Structure

<!-- Multi-entity? Multi-brand? Class/department tracking? Cost centers? System of record. This drives how transactions are coded and reported. -->

**System of Record:** {{qbo_xero_netsuite_etc}}

**Multi-Entity Structure:**
{{single_entity_or_describe_multiple_entities}}

**Class/Department Tracking:**
{{used_or_not_used}}
_If used:_ {{list_classes_or_departments}}

**Location Tracking:**
{{used_or_not_used}}
_If used:_ {{list_locations}}

**Cost Centers:**
{{used_or_not_used}}
_If used:_ {{describe_cost_center_structure}}

**Multi-Brand or Product Lines:**
{{single_brand_or_list_brands_and_how_tracked}}

**Accounting Structure Notes:**
{{any_special_reporting_requirements_or_consolidation_notes}}

---

## Additional Context

<!-- Anything else worth knowing that doesn't fit above: special compliance requirements, unique business quirks, history of prior accounting issues, growth plans, etc. -->

{{any_additional_context_or_remove_this_section}}
