# SCF VARIABLE QUICK REFERENCE
## Key Variables for Materialism and Marriage Research

**Source:** Survey of Consumer Finances Codebook  
**Note:** Variable names may differ slightly between waves (check codebook)

---

## 🎯 ESSENTIAL VARIABLES

### **DEPENDENT VARIABLE: Marital Status**

| Variable | Description | Coding | Notes |
|----------|-------------|--------|-------|
| `MARRIED` | Current marital status | 1=Married, 2=Living with partner, 3=Separated, 4=Divorced, 5=Widowed, 6=Never married | Recode to binary: 1=married, 0=not |
| `AGE` | Age of respondent | Continuous (18-95+) | Use to create "married by age X" |

---

### **MATERIALISM INDICATORS (Behavioral Proxies)**

#### **Consumer Debt (Primary Indicators):**

| Variable | Description | Typical Range | Why It Measures Materialism |
|----------|-------------|---------------|----------------------------|
| `DEBT` | Total household debt | $0 - $500K+ | High debt indicates consumption beyond means |
| `CCBAL` | Credit card balance | $0 - $50K+ | Credit cards enable immediate material gratification |
| `INSTALL` | Installment debt | $0 - $100K+ | Auto loans, furniture, appliances = material purchases |
| `ODEBT` | Other debt | $0 - $50K+ | Additional consumer debt |
| `NH_MORT` | Non-housing debt | $0 - $100K+ | Debt for consumption (not investment) |

#### **Material Possessions:**

| Variable | Description | Range | Materialism Indicator |
|----------|-------------|-------|----------------------|
| `VEHIC` | Number of vehicles | 0-5+ | More vehicles = more material possessions |
| `HOUSES` | Number of homes | 0-3+ | Multiple properties (not just primary residence) |
| `ORESRE` | Other real estate | 0/1 | Vacation homes, investment properties |

#### **Spending vs. Saving:**

| Variable | Description | Notes |
|----------|-------------|-------|
| `SAVING` | Household saving | If available (some waves only) |
| `SAVRES1` | Reason for saving | Material goals vs. emergency/retirement |
| `SPENDMOR` | Spend more than income | Direct materialism indicator! |

---

### **ECONOMIC RESOURCES (Controls & Capstone Marriage)**

| Variable | Description | Use |
|----------|-------------|-----|
| `INCOME` | Total household income | Control + capstone marriage indicator |
| `WAGEINC` | Wage/salary income | Primary earnings (vs. investment income) |
| `NETWORTH` | Total net worth | Assets - Liabilities |
| `LIQ` | Liquid assets | Checking + savings + easily accessible |
| `CHECKING` | Checking account balance | Immediate resources |
| `SAVING` | Savings account balance | Emergency fund |

---

### **EDUCATION (Capstone Marriage + Control)**

| Variable | Description | Coding |
|----------|-------------|--------|
| `EDUC` | Education level | 1=No HS, 2=HS, 3=Some college, 4=College degree |
| `EDCL` | Currently enrolled | 1=Yes, 0=No - signals delaying marriage |

**Recode for Analysis:**
```R
college_degree = ifelse(EDUC >= 4, 1, 0)
```

---

### **EMPLOYMENT (Economic Stress + Capstone)**

| Variable | Description | Coding |
|----------|-------------|--------|
| `OCCCAT` | Occupation category | 1=Managerial, 2=Technical, 3=Service, etc. |
| `EMPSTAT` | Employment status | 1=Working, 2=Unemployed, 3=Retired, etc. |
| `SELFEMP` | Self-employed | 1=Yes, 0=No |
| `HREMP` | Hours worked per week | Continuous |

---

### **DEMOGRAPHICS (Controls)**

| Variable | Description | Coding |
|----------|-------------|--------|
| `AGE` | Age | Continuous |
| `SEX` | Gender | 1=Male, 2=Female |
| `RACE` | Race/ethnicity | 1=White, 2=Black, 3=Hispanic, 4=Other |
| `KIDS` | Number of children | 0-10+ |
| `HHSEX` | Head of household sex | For household structure |

---

### **GEOGRAPHIC (Controls)**

| Variable | Description | Coding |
|----------|-------------|--------|
| `REGION` | Census region | 1=Northeast, 2=Midwest, 3=South, 4=West |
| `SMSA` | Metro area | 0=Rural, 1=Urban/suburban |

---

### **SAMPLE WEIGHTS (CRITICAL)**

| Variable | Description | Use |
|----------|-------------|-----|
| `WGT` or `WGT1` | Sample weight | **Always use in analyses** |
| `WGT2` - `WGT5` | Replicate weights | For proper variance estimation (5 implicates) |

**Important:** SCF uses multiple imputation. Public use file has 5 implicates (complete datasets) to handle missing values.

---

## 🔍 VARIABLE SELECTION STRATEGY

### **Step 1: Identify Available Variables**

```R
# Load data
scf <- read_dta("data/scf2022.dta")

# See all variable names
names(scf)

# Search for specific concepts
names(scf)[grepl("DEBT", names(scf))]  # All debt variables
names(scf)[grepl("MARR", names(scf))]  # All marriage variables
```

### **Step 2: Check Codebook**

- Download PDF codebook from Fed website
- Search for keywords: "debt", "materialism", "consumption", "marriage"
- Note variable codes and coding schemes

### **Step 3: Validate Variable Quality**

```R
# Check missingness
summary(scf$CCBAL)

# Check distribution
hist(scf$CCBAL)

# Check by marriage status
scf %>% group_by(MARRIED) %>% summarise(mean_ccbal = mean(CCBAL, na.rm=T))
```

---

## 💡 CONSTRUCTING MATERIALISM INDEX

### **Recommended Approach:**

**Core Components (Strongest Materialism Indicators):**
1. ✅ **Credit card balance** (`CCBAL`)
2. ✅ **Installment debt** (`INSTALL`)
3. ✅ **Debt-to-income ratio** (`DEBT / INCOME`)
4. ✅ **Number of vehicles** (`VEHIC`)

**Optional Components (If Available):**
- **Recently bought new car** (`NEWCAR`) - recent material purchase
- **Spending rate** (`SPENDING / INCOME`) - consumption vs. income
- **Luxury items** - jewelry, boats, RVs (if tracked)

**Formula:**
```R
# Standardize each component
scf <- scf %>%
  mutate(
    z_ccbal = scale(log(CCBAL + 1))[,1],
    z_install = scale(log(INSTALL + 1))[,1],
    z_vehic = scale(VEHIC)[,1],
    z_debt_income = scale(DEBT / (INCOME + 1))[,1]
  )

# Average standardized components
scf <- scf %>%
  mutate(
    materialism_index = (z_ccbal + z_install + z_vehic + z_debt_income) / 4
  )
```

---

## 📋 ALTERNATIVE SCF DATASETS

### **If Main SCF Doesn't Work:**

**Option 1: PSID (Panel Study of Income Dynamics)**
- Longitudinal (follows same people over time)
- Can observe marriage transitions
- Has wealth/debt data
- Website: https://psidonline.isr.umich.edu/

**Option 2: NLSY (National Longitudinal Survey of Youth)**
- Follows young adults over time
- Rich marriage timing data
- Some financial data (but less than SCF)
- Website: https://www.nlsinfo.org/

**Option 3: Add Health and Retirement Study (HRS)**
- For older adults (50+)
- Can study materialism and marriage among older never-married
- Longitudinal

---

## 📚 CITATION

### **How to Cite SCF in Your Paper:**

> **Data Source:**
> Survey of Consumer Finances, 2019 and 2022. Board of Governors of the Federal Reserve System. Retrieved from https://www.federalreserve.gov/econres/scfindex.htm

> **Software:**
> Analysis conducted in R version 4.3.1 (R Core Team, 2023) using the survey package (Lumley, 2020) for weighted regression analysis.

---

## 🎯 EXPECTED FINDINGS

### **Hypothesized Results:**

**Main Effect:**
- Materialism index → **Negative coefficient** (β ≈ -0.30 to -0.50)
- Higher materialism → Lower probability of marriage
- **Supports your thesis**

**Subgroup Effects:**
- Effect stronger for **middle-income** (resources matter)
- Effect stronger for **college-educated** (higher marriage standards)
- Effect varies by **race** (cultural differences)

**Robustness:**
- Finding consistent across alternative index specifications
- Finding holds in multiple age cohorts
- Finding robust to different control variables

---

## ✅ READY TO ANALYZE

**You now have:**
- ✅ Complete R code for SCF analysis (7 scripts)
- ✅ All necessary functions and utilities
- ✅ Publication-ready table and figure templates
- ✅ Comprehensive documentation

**Next steps:**
1. Download SCF data from Federal Reserve
2. Run `00_MASTER_RUN_ALL.R`
3. Review outputs
4. Write up results

**Total time: 2-3 days from data download to draft results section**

---

**This will provide empirical evidence for your thesis using high-quality national data!** 📊💍
