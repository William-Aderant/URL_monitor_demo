# Nova 2 Lite vs Textract+Claude Comparison Report

**Generated:** 2026-01-29 14:29:15

## Overall Statistics

| Metric | Value |
|--------|-------|
| Total PDFs Tested | 20 |
| Textract+Claude Success | 20/20 (100.0%) |
| Nova 2 Lite Success | 20/20 (100.0%) |
| Both Succeeded | 20/20 (100.0%) |

## Accuracy (when both succeeded)

| Metric | Value |
|--------|-------|
| Titles Match | 14/20 (70.0%) |
| Form Numbers Match | 19/20 (95.0%) |

## Performance

| Metric | Textract+Claude | Nova 2 Lite |
|--------|-----------------|-------------|
| Average Time | 3.6s | 4.47s |
| Total Time | 72.0s | 89.4s |
| **Speedup** | - | **0.81x slower** |
| Nova Faster In | - | 10/20 (50.0%) |

## Confidence Scores

| Metric | Textract+Claude | Nova 2 Lite |
|--------|-----------------|-------------|
| Average Confidence | 0.930 | 0.950 |
| Min Confidence | 0.836 | 0.950 |
| Max Confidence | 0.999 | 0.950 |

## Differences Found

**7 PDFs had different results:**

### 1. Alaska CIV-775

- **Version ID:** 1
- **File Size:** 41.9 KB
- **PDF Path:** `data/pdfs/5/1/original.pdf`

**Title Difference:**
- Textract+Claude: `Request And Order For Central Calendaring (Crim R 351 Uniform Calendaring Order)`
- Nova 2 Lite: `Request And Order For Central Calendaring`

**Confidence:** TC=0.920, Nova=0.950

---

### 2. Alaska CIV-106

- **Version ID:** 2
- **File Size:** 791.9 KB
- **PDF Path:** `data/pdfs/6/2/original.pdf`

**Title Difference:**
- Textract+Claude: `How To Serve A Summons In A Civil Lawsuit`
- Nova 2 Lite: `How To Serve A Summons`

**Confidence:** TC=0.921, Nova=0.950

---

### 3. Alaska CIV-531

- **Version ID:** 3
- **File Size:** 132.9 KB
- **PDF Path:** `data/pdfs/7/3/original.pdf`

**Title Difference:**
- Textract+Claude: `Claim Of Exemption From Garnishment (As 0938050(B))`
- Nova 2 Lite: `Claim Of Exemption From Garnishment`

**Confidence:** TC=0.839, Nova=0.950

---

### 4. Alaska CIV-760

- **Version ID:** 15
- **File Size:** 32.4 KB
- **PDF Path:** `data/pdfs/19/15/original.pdf`

**Title Difference:**
- Textract+Claude: `Application For Post Conviction Relief From Conviction Or Sentence (Criminal Rule 351)`
- Nova 2 Lite: `Application For Post Conviction Relief From Convictionsentence (Criminal Rule 351)`

**Confidence:** TC=0.998, Nova=0.950

---

### 5. Alaska CIV-575

- **Version ID:** 16
- **File Size:** 145.7 KB
- **PDF Path:** `data/pdfs/20/16/original.pdf`

**Title Difference:**
- Textract+Claude: `Writ Of Assistance (Not Valid Without Court Seal)`
- Nova 2 Lite: `Writ Of Assistance`

**Confidence:** TC=0.859, Nova=0.950

---

### 6. Alaska CIV-100

- **Version ID:** 18
- **File Size:** 137.0 KB
- **PDF Path:** `data/pdfs/22/18/original.pdf`

**Title Difference:**
- Textract+Claude: `Summons`
- Nova 2 Lite: `Summons And Notice To Both Parties Of Judicial Assignment`

**Confidence:** TC=0.916, Nova=0.950

---

### 7. Alaska VS-405

- **Version ID:** 19
- **File Size:** 52.9 KB
- **PDF Path:** `data/pdfs/23/19/original.pdf`

**Form Number Difference:**
- Textract+Claude: `VS-405 06-5422`
- Nova 2 Lite: `VS-405`

**Confidence:** TC=0.882, Nova=0.950

---

## All Results

| Version ID | URL Name | TC Title | Nova Title | TC Form | Nova Form | TC Time | Nova Time | Match |
|------------|----------|----------|------------|---------|-----------|---------|-----------|-------|
| 1 | Alaska CIV-775 | Request And Order For Central  | Request And Order For Central  | CIV-775 | CIV-775 | 3.88s | 3.01s | ✗ |
| 2 | Alaska CIV-106 | How To Serve A Summons In A Ci | How To Serve A Summons | CIV-106 | CIV-106 | 3.28s | 14.47s | ✗ |
| 3 | Alaska CIV-531 | Claim Of Exemption From Garnis | Claim Of Exemption From Garnis | CIV-531 | CIV-531 | 3.88s | 3.33s | ✗ |
| 4 | Alaska CIV-563 | Affidavit Return Of Service Fo | Affidavit Return Of Service Fo | CIV-563 | CIV-563 | 3.26s | 4.76s | ✓ |
| 5 | Alaska CIV-622 | Affidavit Of Attempted Service | Affidavit Of Attempted Service | CIV-622 | CIV-622 | 3.93s | 3.32s | ✓ |
| 6 | Alaska CIV-702 | Affidavit Of Additional Servic | Affidavit Of Additional Servic | CIV-702 | CIV-702 | 3.81s | 3.52s | ✓ |
| 7 | Alaska CIV-515 | Claim Of Exemptions | Claim Of Exemptions | CIV-515 | CIV-515 | 3.76s | 3.04s | ✓ |
| 8 | Alaska CIV-537 | Claim Of Exemptions For Proper | Claim Of Exemptions For Proper | CIV-537 | CIV-537 | 3.13s | 2.84s | ✓ |
| 9 | Alaska CIV-730 | Complaint For Forcible Entry A | Complaint For Forcible Entry A | CIV-730 | CIV-730 | 4.46s | 3.97s | ✓ |
| 10 | Alaska CIV-562 | Affidavit Return Of Service Fo | Affidavit Return Of Service Fo | CIV-562 | CIV-562 | 3.56s | 3.47s | ✓ |
| 11 | Alaska CIV-410 | Cost Bill | Cost Bill | CIV-410 | CIV-410 | 3.38s | 4.55s | ✓ |
| 12 | Alaska CIV-790 | Application For Ex Parte Order | Application For Ex Parte Order | CIV-790 | CIV-790 | 3.64s | 4.22s | ✓ |
| 13 | Alaska CIV-105 | Summons Forcible Entry And Det | Summons Forcible Entry And Det | CIV-105 | CIV-105 | 3.41s | 4.15s | ✓ |
| 14 | Alaska CIV-735 | Answer To Forcible Entry And D | Answer To Forcible Entry And D | CIV-735 | CIV-735 | 3.28s | 4.82s | ✓ |
| 15 | Alaska CIV-760 | Application For Post Convictio | Application For Post Convictio | CIV-760 | CIV-760 | 3.89s | 5.73s | ✗ |
| 16 | Alaska CIV-575 | Writ Of Assistance (Not Valid  | Writ Of Assistance | CIV-575 | CIV-575 | 3.99s | 3.23s | ✗ |
| 17 | Alaska CIV-585 | Writ Of Execution For Bank Swe | Writ Of Execution For Bank Swe | CIV-585 | CIV-585 | 3.16s | 3.26s | ✓ |
| 18 | Alaska CIV-100 | Summons | Summons And Notice To Both Par | CIV-100 | CIV-100 | 3.34s | 3.4s | ✗ |
| 19 | Alaska VS-405 | Application For Legal Name Cha | Application For Legal Name Cha | VS-405 06-5422 | VS-405 | 3.56s | 3.13s | ✗ |
| 20 | Alaska CIV-481 | Answer Counterclaim To Complai | Answer Counterclaim To Complai | CIV-481 | CIV-481 | 3.38s | 7.15s | ✓ |

## Recommendation

**⚠ Review Required** - Significant differences found, manual review recommended
