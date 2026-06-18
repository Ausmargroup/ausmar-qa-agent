# AUSMAR PSE Tool Reverse Engineering Document

This document provides a comprehensive technical breakdown of the AUSMAR Provisional Sales Estimate (PSE) Excel tool (`PSEJune2026v2.xlsb`). It maps the entire data flow, formula architecture, VBA macros, and sheet relationships, providing a complete blueprint for rebuilding or migrating the system to a modern web or database application.

![Architecture Diagram](https://private-us-east-1.manuscdn.com/sessionFile/gKrUfUN5AXvrs5cnHiFP4t/sandbox/N0ViaTcBh0qo2d1FghHLEJ-images_1781737043478_na1fn_L2hvbWUvdWJ1bnR1L2FyY2hpdGVjdHVyZQ.png?Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9wcml2YXRlLXVzLWVhc3QtMS5tYW51c2Nkbi5jb20vc2Vzc2lvbkZpbGUvZ0tyVWZVTjVBWHZyczVjbkhpRlA0dC9zYW5kYm94L04wVmlhVGNCaDBxbzJkMUZnaEhMRUotaW1hZ2VzXzE3ODE3MzcwNDM0NzhfbmExZm5fTDJodmJXVXZkV0oxYm5SMUwyRnlZMmhwZEdWamRIVnlaUS5wbmciLCJDb25kaXRpb24iOnsiRGF0ZUxlc3NUaGFuIjp7IkFXUzpFcG9jaFRpbWUiOjE3OTg3NjE2MDB9fX1dfQ__&Key-Pair-Id=K2HSFNDJXOU9YS&Signature=tHchcnC4lF5kF1WAiXSuxDK4rsxwPtSu-gz7kZmEcD3Lv41iDuAKQ6oJiBxiASmy8TPNXtI6W-alueuRhamayWxfoNASNlVorc3PgvAZu~fH5fw1btTV3YbYKa1GnfCjWHXTcmxXMec9kT7Ft8IYXS0u7GKzDw~ryYO7lafr1DbGRzm7GIX6qio06AxoivySiO3CnP4TyA6H4HwGOsP88cYPzWi6jKxEFojBjrYUmYF~0XYVgSfZfvyJy9DtRYwUidQn0nUL25eBTJhze1N5cXCPFkvF9n4lZMhSNYjvtSuiTumnMa3vKqE5rPuIFPcPS8dhwv9YC0IKInVu7jt2Vw__)

## 1. System Architecture Overview

The PSE tool is a massive, formula-driven pricing engine wrapped in an Excel interface. It relies on a "hub and spoke" architecture where a central pricing matrix (`Sell Price Sheet`) is pre-calculated for every possible combination of Design and Façade. The user-facing form (`PSE-NHP`) then uses `HLOOKUP` to pull prices dynamically based on the user's selection.

### Key Characteristics
- **Formula-Driven Pricing**: VBA is **not** used for any pricing calculations. The entire pricing logic relies on ~60,000 Excel formulas.
- **Print-Driven VBA**: The extensive VBA code (`vbaProject.bin`) is almost exclusively used for orchestrating PDF generation (hiding rows marked "No" via AutoFilter, then printing specific sheet combinations).
- **Composite Key Lookup**: The core mechanism joining the form to the pricing engine is a concatenated string: `CONCATENATE(Plan, " ", Façade)` (e.g., "Malibu 402 Hamptons").

---

## 2. Data Flow & Sheet Breakdown

The workbook contains 33 sheets, which can be categorized into five distinct layers.

### Layer 1: Data Sources (The "Truth")
These sheets hold raw data imported from external systems or entered manually by the estimator.
*   **`DB Import`**: Raw base cost data exported from Databuild. Columns S, T, and U contain the Job Cost Summary (e.g., S = "Arcadia Traditional", U = $310,866.27).
*   **`DB Windows` & `DB Screens`**: Raw Bill of Quantities data for glazing and screens.
*   **`Input same price all homes`**: Manual cost overrides and standard item costs (e.g., air conditioning units, appliances, standard electrical). Column B is the old cost, Column C is the new cost.
*   **`SQM`**: A massive matrix defining the physical quantities for every design (e.g., Slab Floor Area, Brickwork linear meters, Tiling areas).

### Layer 2: The Pricing Engine
This is where costs are marked up to become sell prices.
*   **`Cost Price Sheet`**: 
    *   Pulls base costs via `VLOOKUP` from `DB Import`.
    *   Pulls standard item costs from `Input same price all homes`.
    *   **Margin Logic**: Rows 0-3 define the global margins. The primary gross-up factor is calculated in cell `L1`: `1.1 * (1 + B1 + B2 + B3)`. 
        *   `B1` = Base Margin (24%)
        *   `B2` = Adv/Marketing/Selling (3.6%)
        *   `B3` = Global Cost Movement (4.65%)
        *   Total multiplier = 1.45475 (inclusive of 10% GST).
*   **`Sell Price Sheet`**: 
    *   The largest sheet in the workbook (63 rows × 746 columns, ~27,000 formulas).
    *   Row 9 contains the concatenated Design + Façade headers (e.g., "Arcadia Traditional").
    *   Applies the `L1` gross-up factor to the `Cost Price Sheet` values to generate final, GST-inclusive retail prices for every item across every home design.
*   **`Master Pricing`**: Extracts just the base house prices from the `Sell Price Sheet` for quick reference.

### Layer 3: Category Dictionaries
These sheets act as lookup tables for specific upgrade categories, holding descriptions and Databuild codes.
*   **`Plumbing`, `Doors`, `Colour Upgrades`, `Facade`, `Appliances`, `Floor Coverings`, `Bricks`, `Caesarstone`, `Electrical Inclusions`, `Upgrade Packages`**.
*   These sheets cross-reference the user's selections to determine if an item is "Included" in the base specification or if it incurs an upgrade charge.

### Layer 4: User Interface (The Form)
*   **`PSE-NHP`**: The main interface used by sales consultants. 
    *   **Row 11/12**: The user selects the Plan (`C11`) and Façade (`C12`) via dropdowns.
    *   **Row 19 (`I19`)**: Base price is fetched via `=HLOOKUP(CONCATENATE($C$11," ",$C$12), 'Sell Price Sheet'!$B$10:$ABR$46, 2, FALSE)`.
    *   **Rows 20 to 1168**: The line items. Each row has a "Yes/No" toggle in Column A.
    *   **Columns I & J (Debit/Credit)**: If Column A is "Yes", a formula in Column I does an `HLOOKUP` to the `Sell Price Sheet` to fetch the upgrade cost. Column J handles credits (e.g., removing an included item).
    *   **Columns M & N (Manual Override)**: Consultants can manually type a debit/credit here, which overrides the formula lookup.
    *   **Row 1227**: The grand total (`PROPOSED NEW HOME PRICE`) = `SUM(I20:I1168) - SUM(J20:J1168)`.
*   **`Changes`**: A summary sheet that aggregates all rows from `PSE-NHP` where a variation (Debit or Credit) exists.
*   **`Margin`**: A backend sheet that divides the final sell price by the `L1` gross-up factor to back-calculate the order cost and verify the actual profit margin (target is 32.25%).
*   **`Commission`**: Calculates salesperson and external investor group commissions based on the job type.

### Layer 5: Output & Presentation
*   **`PSE Cover Page`, `NHP Cover Page`, `Spec Cover Page`, `Index`, `Notes`, `Rego`**: Formatting and layout sheets that pull top-level data (client name, site address, total price) from `PSE-NHP` for the final PDFs.

---

## 3. Key Formula Mechanics

To rebuild this tool, the following formula patterns must be replicated in code:

### 1. The Gross-Up (Margin) Calculation
```excel
Sell Price = ROUNDUP(Cost * (1 + BaseMargin + Marketing + CostMovement) * 1.1, 0)
```
*(Located in `Sell Price Sheet` referencing `Cost Price Sheet` and `Input same price all homes`)*

### 2. The Universal Price Lookup
```excel
=IF(A231="Yes", HLOOKUP(CONCATENATE($C$11," ",$C$12), 'Sell Price Sheet'!$B$10:$ABR$46, [RowIndex], FALSE), 0)
```
*(Located throughout `PSE-NHP` Column I. If the user toggles the item to "Yes", it fetches the price for the specific house design).*

### 3. Area-Based Pricing (SQM Multipliers)
```excel
=IF(A996="Yes", (HLOOKUP($C$11, SQM!$B$2:$DR$136, [AreaRow], FALSE) * CostPerSqm * MarginFactor), 0)
```
*(Used for items like floor coverings, render, and air conditioning sizing, where the price depends on the square meterage of the specific design).*

---

## 4. VBA Macro Operations

Because `pyxlsb` cannot extract VBA, the `vbaProject.bin` was extracted using `oletools` and analyzed. The VBA code does **not** perform calculations. It is strictly an output orchestrator.

The workbook contains 36 VBA modules (e.g., `Sheet1.cls`, `Sheet7.cls`, `Module1.bas`). The core logic is identical across the action buttons:

1.  **Unprotect Sheets**: `Sheet1.Unprotect Password:="mullac77"`
2.  **Optimize Performance**: Disables ScreenUpdating, Calculation, and EnableEvents.
3.  **Filter "No" Rows**: Uses `AutoFilter` on specific columns (usually Column A or J/M depending on the sheet) to hide any row where the value is "No" or "N/A".
    ```vba
    .Range("A20:A971").AutoFilter Field:=1, Criteria1:="No"
    Selection.EntireRow.Hidden = True
    ```
4.  **Print to PDF**: Selects an array of sheets (e.g., `PSE-NHP`, `Notes`, `Changes`) and calls `.PrintOut Copies:=1`.
5.  **Cleanup**: Unhides the rows, restores settings, and re-protects the sheets with the password `mullac77`.

---

## 5. Rebuild Recommendations

If AUSMAR intends to rebuild this tool as a web application (e.g., using React/Node.js or a low-code platform), the architecture should be transformed as follows:

1.  **Database Structure**:
    *   `Designs` Table (Plan Name, Façade Name, Base Cost, SQM metrics).
    *   `Options` Table (Category, Description, Unit Cost, Unit Type [Fixed, Per SQM, Per LM]).
    *   `Global Settings` Table (Base Margin %, Marketing %, Cost Movement %, GST).
2.  **Dynamic Calculation**: Instead of a 27,000-cell matrix (`Sell Price Sheet`), calculate prices on the fly. When a user selects "Malibu 402 Hamptons", the app fetches the base cost and SQM metrics, applies the global margin multipliers, and renders the form.
3.  **Output Generation**: Replace the VBA AutoFilter macros with a PDF generation library (like Puppeteer or PDFKit) that simply iterates through the selected options and renders an HTML template.
4.  **Integration**: Replace the `DB Import` copy-paste process with a direct API integration or CSV upload parser that updates the `Designs` and `Options` tables automatically.
