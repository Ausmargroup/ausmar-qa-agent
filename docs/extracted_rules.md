# AUSMAR PSE QA — Extracted Rules from Official Documents

## 1. PSE Document Naming (from 1.0 PSE Document Naming)

### REQUIRED DOCUMENTS (must be in zip or explained why not):
1. POD or Building Envelope Plan — YES/NO with explanation
2. Compaction Report — YES/NO with explanation
3. Covenant Design Guidelines — YES/NO with explanation
4. Disclosure Plan / Survey Plan — YES/NO with explanation
5. Drivers Licence [Front only] — YES/NO with explanation
6. PSE Excel — YES/NO with explanation
7. PSE Checklist — YES/NO with explanation
8. Discount Approval Email — YES/NO with explanation (conditional)
9. Owner Supplied Items Approval Email — YES/NO with explanation (conditional)
10. Modified Plan Approval — YES/NO with explanation (conditional)

### CLIENT SIGNED DOCUMENTS:
11. Intention to Purchase & Agreement (ITP) — YES/NO with explanation
12. PROMO - Client Acknowledgement — YES/NO with explanation
13. PSE [Signed] — YES/NO with explanation
14. Geosite Plan [Signed] — YES/NO with explanation
15. Covenant Application Form [Signed] — YES/NO with explanation (if covenant)
16. Swimming Pool Form [Signed] — YES/NO with explanation (if pool)
17. PSE Red Pen Markup [Signed] — must include:
    - Floor Plan
    - Elevations
    - Electrical Plan
    - Floor Coverings Plan
    - Concrete Plan
18. Sites with Fall Acknowledgment [Signed] — if slope >= 500mm

## 2. Conditional Documents (when required):
- Discount Approval Form: needed when any discount given. Must be emailed to Director for approval.
- Owner Supplied Items Approval Form: needed when client supplies own items. Must be emailed to Manager.
- Modified Plan Approval: needed when plan is modified from standard. Must be emailed to Sales Manager.
- Sites with Fall Acknowledgment: needed when slope across site >= 500mm
- Covenant Application Form: needed when estate has covenant
- Swimming Pool Form: needed when pool included
- PROMO Client Acknowledgement: needed when sales promotion applied (Advantage, Super Saver, etc.)

## 3. Sales Accept Checklist (from 1.3 Sales Accept):
- Price sheet is the correct month
- Extended land rego has been captured
- All documents as per 1.0 PSE Document Naming
- All signed documents uploaded to HUBSPOT
- Discount Approval Form (if applicable)
- Red pen markup numbered as per PSE
- All items in PSE are allowable
- Plan meets allowable changes guidelines
- Modified Plan Approval (if applicable)
- Owner Supplied Items Approval Form (if applicable)
- Prelim Deposit Receipt
- Sales Promotion – Client Acknowledgement Signed (if applicable)

## 4. Sites with Fall Rules (from 1.1 Sites with Fall):
- Max cut/fill before building manager approval: 1000mm
- Max retaining wall height: 1000mm
- Standard cut/fill ratio: 40% cut to 60% fill
- Standard batter ratio: 1:1 or 45 degrees
- Low-set build minimum pad around house: 900mm
- High-set build minimum pad around house: 1500mm
- Any area cut/filled over 600mm should be retained (unless 45° batter allowed)
- Retaining walls must be 200mm inside boundary
- Walls over 1000mm height need 1500mm setback from boundary + neighbour approval
- Retaining walls must be clear of easements
- Walls must be 1200mm clear of stormwater/sewer mains
- Walls must be 1000mm clear of stormwater/sewer connection points
- Double stacking retaining walls must be discussed with drafting manager first
- AUSMAR uses sleeper-type retaining walls (concrete sleepers + steel posts)
- Standard sleepers: 200mm high x 2000mm long x 50mm wide, planned in 200mm increments
- Council requirements vary by council area (Sunshine Coast, Moreton Bay, Noosa, Fraser Coast, Gympie)

## 5. Real Sales Accept Issues (from salesacceptissues):

### PATTERN: NHP vs STC Confusion
- Multiple jobs (Sh26TGCH, Sh26MGVC, Sh26NGCR) had initial email saying NHP but $4,000 deposit + ITP said STC
- Root cause: wholesale groups take larger deposits; wrong ITP report used
- QA CHECK: Flag when deposit amount doesn't match stream (NHP=$2,500, STC=$4,000) but note wholesale exception

### PATTERN: Acoustic CAT Misidentification
- Consultants using acoustic MAP instead of LOT-SPECIFIC TABLE
- Sh26TGCH: CAT1 noted on PSE but acoustic report showed not required for that lot
- Sh26MGVC: CAT1 required per lot-specific report but no allowance in PSE
- QA CHECK: Flag acoustic category discrepancies, recommend using lot-specific tables not maps

### PATTERN: Pre-lodgement Advice Not Ordered
- S26TLS: Should have ordered pre-lodgement for sticky site with fall + acoustics
- Rule: Pre-lodge needed for: new development areas, sites with lots of fall, acoustics in small developments, random subdivisions, every knock-down rebuild
- QA CHECK: Flag when pre-lodge may be needed based on site characteristics

### PATTERN: GeoSite Quality Issues
- S26TLS: Contours and GeoSite combined making contours unreadable; no setbacks shown
- S25MLS: Wrong scale used, front setback incorrect, text overlapping, contours don't fit pad
- QA CHECK: GeoSite must be separate from site visit doc; must have clear setbacks; must be to scale

### PATTERN: Red Pen Markup Issues
- S26TLS: Red pen not on AUSMAR standard plan, difficult for drafting team
- S25MLS: Red pen had no dimensions
- S26SDN: Red pen tags (e.g. 3.2.a) don't match PSE section references; dimensions missing from changed areas
- S26SDN: Windows deleted from plan but not noted in PSE; door changes not mentioned
- QA CHECK: Red pen MUST be on AUSMAR standard plan in red; all changed areas must have dimensions; tags must match PSE sections

### PATTERN: PSE Completeness Issues
- S26SDN: Façade changes not noted in PSE; linea to façade extent not specified
- S26SDN: Laundry space insufficient (1930mm between walls, need 1400mm for washer+dryer + 700mm for cabinet/sink)
- QA CHECK: All changes shown on red pen must be captured in PSE with specifics

### PATTERN: Battle-axe / LHDC Assessment
- S26PSD: Block looked like battle-axe, unclear if liable housing design standards apply
- QA CHECK: Flag unusual lot shapes that may require LHDC assessment

## 6. Site Visit Checklist Items (from 1.2):
Must check: Power turret, Existing Structures, Existing Cut/Fill Batters, Telstra, Views, North orientation, Existing Retaining Walls, Easements, Stormwater Pits, Power Poles, Street Trees, Sewer Manholes, Culverts in kerbs, Neighbouring house, Existing Fencing, Direction of fall, Proposed house position, Future pool position, Shared driveway, No standing/clearway, Proximity to schools/childcare, Pedestrian crossing, Covenant status, Footpath width

## 7. Heath's Real Review Feedback Patterns (from .msg emails)

### S25RPMT (Telford) — REJECTED
- Solar PV excluded but mandatory per Kinma Valley covenant Section 5.1 (min 1kW)
- Acoustic report doesn't cover the specific lot (wrong stages)
- Gas cooktop + LPG HWS included — need to verify covenant gas ban
- Selection sheet signatures incomplete (pages 16-17 name fields blank)
- Heath response: acoustics not required per report; solar was overlook, minimum 4.44kW system (12 panels)
- QA PATTERN: Check covenant requirements for solar, gas bans; verify acoustic report covers the specific lot

### S26JW (Caitlyn / Baltimore 400 Modern / 14 Gloria St) — Initially ACCEPTED, Heath REJECTED
- Nikole accepted but Heath rejected: needs contour survey to assess site fall, needs survey plan/plan of sub for boundaries
- Highset on 510m² established lot, no covenant
- Missing docs: Disclosure, Promo Ack, PSE Checklist
- Significant site works: 501-1200mm fall, $18K retaining PS, $5K soil PS
- QA PATTERN: Established lots need contour survey; significant fall requires proper survey documentation

### S26SBSH (Ian / Hillcrest Coastal / Lot 3082 Gagalba) — ACCEPTED with items
- 200mm structural extension (3.2A) not dimensioned on Red Pen
- Pantry swap (corner to standard) not explicitly marked on Red Pen
- Cooktop ceramic electric (compliant), site coverage 53.5%
- Kitchen redesign with 300mm extensions, pot/cutlery drawers, window change
- Heath confirmed: 200mm extension shown on red pen, pantry shown is fine
- QA PATTERN: All structural changes must be dimensioned on red pen; pantry swaps should be marked

### S26JYTC (Ian / Hillcrest Coastal / Lot 3124 Gagalba) — ACCEPTED with items
- GeoSite says "Traditional" but PSE and Red Pen say "Coastal" — facade label mismatch
- Porch area 2.64m² vs covenant minimum 3m² — needs verification
- WP Double GPO: PSE says 2, Red Pen shows 1 — quantity mismatch
- Cooktop ceramic electric (compliant), site coverage 59.9% (max 60%, zero margin)
- Heavily modified: bedroom relocation, ensuite reconfiguration, kitchen redesign
- Heath: wants LHDC identification section at top; GeoSite fine (only traditional in program); can measure to under eave
- QA PATTERN: Check facade name consistency across all docs; verify porch area vs covenant minimums; check PSE vs Red Pen quantity matches; flag near-max site coverage

### S26MP (Telford) — REJECTED
- Gas cooktop in Aura estate (Banya). PSE includes Bosch 90cm gas cooktop ($1,336) + gas fitting ($1,003)
- Covenant provided is Acacia but Gagalba bans gas — need to verify which precinct rules apply
- Second buyer signature completely missing (Simolee Patel not signed)
- QA PATTERN: Aura/Gagalba estates likely ban gas; verify all buyers have signed; check correct covenant precinct

### MKR feedback
- Stanmore design doesn't have Hampton upgrade as standard — must be manually listed in spec
- Facade chosen doesn't exist on that design
- Heath: "no dramas we can do that, would be nice for the excel to be right"
- QA PATTERN: Verify facade exists for the chosen design; check facade upgrades are listed in spec if non-standard

## 8. Key QA Rules Summary (for engine implementation)

### CRITICAL CHECKS (rejection-worthy):
1. Plan doesn't fit lot (width/length insufficient)
2. GeoSite not from geosite.com.au tool
3. GeoSite missing setbacks
4. Red Pen not in red / not on AUSMAR base plan / no dimensions
5. Missing critical documents (PSE Signed, GeoSite Signed, ITP, Deposit Receipt)
6. All buyer signatures missing
7. Covenant breaches (gas ban, solar requirements, site coverage)
8. Contour survey needed but not provided (established lots with fall)

### IMPORTANT CHECKS (accepted with concerns):
1. NHP/STC stream mismatch with deposit amount
2. Acoustic report doesn't cover specific lot
3. Red pen tags don't match PSE section references
4. Facade name inconsistent across documents
5. PSE vs Red Pen quantity/spec mismatches
6. Near-maximum site coverage (>58%)
7. Significant site fall (>500mm) without sites-with-fall acknowledgment
8. Missing conditional documents without explanation

### ADMIN CHECKS (auto-fixable):
1. File naming not matching convention
2. Subfolder structure in zip
3. Junk files (.msg, .DS_Store, etc.)
4. Title case violations
5. Missing (Signed) suffix
