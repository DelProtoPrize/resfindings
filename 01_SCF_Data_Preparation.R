################################################################################
# SCF Analysis - Data Preparation
# Research Question: Materialism and Delayed Marriage Among Young Adults
#
# This script:
# 1. Loads Survey of Consumer Finances data (2019 + 2022 waves)
# 2. Filters for young adults (ages 18-29)
# 3. Creates key variables
# 4. Prepares data for analysis
#
# Author: Research Project
# Date: November 24, 2025
################################################################################

# ========================================
# LOAD PACKAGES
# ========================================

# Check if packages are installed, install if needed
required_packages <- c("tidyverse", "haven", "survey", "labelled", 
                       "psych", "stargazer", "ggplot2", "scales")

for (pkg in required_packages) {
  if (!require(pkg, character.only = TRUE)) {
    install.packages(pkg, repos = "http://cran.us.r-project.org")
    library(pkg, character.only = TRUE)
  }
}

cat("✓ All packages loaded\n\n")

# ========================================
# CONFIGURATION
# ========================================

# Set working directory (adjust as needed)
# setwd("~/scf_analysis/")

# Data paths
DATA_DIR <- "data/"
OUTPUT_DIR <- "output/"

# Create directories if they don't exist
dir.create(DATA_DIR, showWarnings = FALSE)
dir.create(OUTPUT_DIR, showWarnings = FALSE)

# Age range for young adults
AGE_MIN <- 18
AGE_MAX <- 29

cat("Configuration:\n")
cat(sprintf("  Age range: %d-%d\n", AGE_MIN, AGE_MAX))
cat(sprintf("  Data directory: %s\n", DATA_DIR))
cat(sprintf("  Output directory: %s\n\n", OUTPUT_DIR))

# ========================================
# LOAD SCF DATA
# ========================================

cat("="*80, "\n")
cat("LOADING SCF DATA\n")
cat("="*80, "\n\n")

# NOTE: You need to download SCF data first from:
# https://www.federalreserve.gov/econres/scfindex.htm
# Download the "Summary Extract Public Data" files for 2019 and 2022

# Load 2019 wave
cat("Loading 2019 SCF data...\n")

# Choose appropriate file format based on what you downloaded
# Option 1: Stata format (.dta)
tryCatch({
  scf_2019 <- read_dta(paste0(DATA_DIR, "scf2019.dta"))
  cat(sprintf("✓ 2019 data loaded: %d households\n", nrow(scf_2019)))
}, error = function(e) {
  cat("⚠ Could not load scf2019.dta\n")
  cat("  Download from: https://www.federalreserve.gov/econres/scfindex.htm\n")
  cat("  Place in:", DATA_DIR, "\n")
  scf_2019 <<- NULL
})

# Load 2022 wave
cat("Loading 2022 SCF data...\n")

tryCatch({
  scf_2022 <- read_dta(paste0(DATA_DIR, "scf2022.dta"))
  cat(sprintf("✓ 2022 data loaded: %d households\n", nrow(scf_2022)))
}, error = function(e) {
  cat("⚠ Could not load scf2022.dta\n")
  cat("  Download from: https://www.federalreserve.gov/econres/scfindex.htm\n")
  scf_2022 <<- NULL
})

# ========================================
# HARMONIZE ACROSS WAVES
# ========================================

cat("\nHarmonizing variables across waves...\n")

# Add wave identifier
if (!is.null(scf_2019)) {
  scf_2019 <- scf_2019 %>% mutate(wave = 2019)
}

if (!is.null(scf_2022)) {
  scf_2022 <- scf_2022 %>% mutate(wave = 2022)
}

# Combine waves
if (!is.null(scf_2019) && !is.null(scf_2022)) {
  scf_combined <- bind_rows(scf_2019, scf_2022)
  cat(sprintf("✓ Combined dataset: %d households (2019 + 2022)\n", nrow(scf_combined)))
} else {
  cat("⚠ Using single wave or sample data\n")
  # Create sample data for demonstration
  set.seed(42)
  scf_combined <- tibble(
    wave = sample(c(2019, 2022), 10000, replace = TRUE),
    AGE = sample(18:75, 10000, replace = TRUE),
    MARRIED = rbinom(10000, 1, 0.45),
    INCOME = rlnorm(10000, log(60000), 0.8),
    DEBT = rlnorm(10000, log(20000), 1.2),
    CCBAL = pmax(0, rnorm(10000, 3000, 5000)),
    INSTALL = pmax(0, rnorm(10000, 8000, 10000)),
    VEHIC = sample(0:4, 10000, replace = TRUE, prob = c(0.1, 0.3, 0.4, 0.15, 0.05)),
    EDUC = sample(1:4, 10000, replace = TRUE, prob = c(0.1, 0.3, 0.35, 0.25)),
    RACE = sample(1:4, 10000, replace = TRUE, prob = c(0.6, 0.13, 0.18, 0.09)),
    KIDS = rpois(10000, 0.8),
    WGT = runif(10000, 500, 15000),
    NETWORTH = rnorm(10000, 50000, 100000)
  )
  cat("⚠ Using synthetic data for demonstration\n")
  cat("  Replace with actual SCF data for real analysis\n")
}

# ========================================
# FILTER FOR YOUNG ADULTS
# ========================================

cat("\n", "="*80, "\n")
cat("FILTERING FOR YOUNG ADULTS (", AGE_MIN, "-", AGE_MAX, ")\n")
cat("="*80, "\n\n")

scf_young <- scf_combined %>%
  filter(AGE >= AGE_MIN & AGE <= AGE_MAX)

cat(sprintf("Original sample: %d households\n", nrow(scf_combined)))
cat(sprintf("Young adults (18-29): %d households (%.1f%%)\n", 
            nrow(scf_young), 
            nrow(scf_young)/nrow(scf_combined)*100))

# ========================================
# CREATE ANALYTIC VARIABLES
# ========================================

cat("\n", "="*80, "\n")
cat("CREATING ANALYTIC VARIABLES\n")
cat("="*80, "\n\n")

scf_young <- scf_young %>%
  mutate(
    # ========================================
    # DEPENDENT VARIABLES (Marital Timing)
    # ========================================
    
    # Currently married (binary)
    currently_married = MARRIED,
    
    # Married by age 25 (for those 25+)
    married_by_25 = ifelse(AGE >= 25 & MARRIED == 1, 1, 0),
    
    # Married by age 27 (for those 27+)
    married_by_27 = ifelse(AGE >= 27 & MARRIED == 1, 1, 0),
    
    # Ever married (for all ages 18-29)
    ever_married = MARRIED,
    
    # Age categories
    age_cat = cut(AGE, 
                  breaks = c(18, 22, 25, 29),
                  labels = c("18-21", "22-24", "25-29"),
                  include.lowest = TRUE),
    
    # ========================================
    # MATERIALISM INDICATORS
    # ========================================
    
    # Debt-to-income ratio
    debt_to_income = DEBT / (INCOME + 1),  # +1 to handle zero income
    
    # Log transformations (handle skewness)
    log_ccbal = log(CCBAL + 1),
    log_install = log(INSTALL + 1),
    log_debt = log(DEBT + 1),
    
    # Binary indicators
    has_credit_card_debt = ifelse(CCBAL > 0, 1, 0),
    high_installment_debt = ifelse(INSTALL > 5000, 1, 0),
    multiple_vehicles = ifelse(VEHIC >= 2, 1, 0),
    
    # ========================================
    # ECONOMIC RESOURCES
    # ========================================
    
    # Income (log transform for regression)
    log_income = log(INCOME + 1),
    
    # Income quartiles (within young adult sample)
    income_quartile = ntile(INCOME, 4),
    
    # Net worth
    log_networth = log(abs(NETWORTH) + 1),
    networth_positive = ifelse(NETWORTH > 0, 1, 0),
    
    # ========================================
    # EDUCATION (Recode if Needed)
    # ========================================
    
    # Education categories (adjust based on actual SCF coding)
    # SCF EDUC: 1=No HS, 2=HS, 3=Some College, 4=College Degree
    educ_cat = factor(EDUC,
                      levels = 1:4,
                      labels = c("Less than HS", "High School", 
                                "Some College", "College Degree")),
    
    college_degree = ifelse(EDUC >= 4, 1, 0),
    
    # ========================================
    # DEMOGRAPHICS
    # ========================================
    
    # Race/ethnicity (recode to common categories)
    race_cat = factor(RACE,
                      levels = 1:4,
                      labels = c("White", "Black", "Hispanic", "Other")),
    
    # Has children
    has_children = ifelse(KIDS > 0, 1, 0)
  )

cat("✓ Created marital timing variables\n")
cat("✓ Created materialism indicators\n")
cat("✓ Created economic resource variables\n")
cat("✓ Created demographic controls\n")

# ========================================
# CREATE MATERIALISM INDEX
# ========================================

cat("\nCreating materialism composite index...\n\n")

# Standardize components
scf_young <- scf_young %>%
  mutate(
    # Z-scores for each component
    z_ccbal = scale(log_ccbal)[,1],
    z_install = scale(log_install)[,1],
    z_vehic = scale(VEHIC)[,1],
    z_debt_income = scale(debt_to_income)[,1]
  )

# Composite index (average of standardized components)
# Higher score = more materialistic
scf_young <- scf_young %>%
  mutate(
    materialism_index = (z_ccbal + z_install + z_vehic + z_debt_income) / 4,
    
    # Tertiles for descriptive analysis
    materialism_tertile = ntile(materialism_index, 3),
    
    # Binary: High materialism (top tertile)
    materialism_high = ifelse(materialism_tertile == 3, 1, 0)
  )

cat("✓ Materialism index created\n")
cat("  Components: Credit card debt, Installment debt, Vehicles, Debt-to-income\n")
cat("  Range:", round(min(scf_young$materialism_index, na.rm=T), 2), "to", 
    round(max(scf_young$materialism_index, na.rm=T), 2), "\n")
cat("  Mean:", round(mean(scf_young$materialism_index, na.rm=T), 2), "\n")
cat("  SD:", round(sd(scf_young$materialism_index, na.rm=T), 2), "\n")

# ========================================
# DESCRIPTIVE STATISTICS
# ========================================

cat("\n", "="*80, "\n")
cat("DESCRIPTIVE STATISTICS\n")
cat("="*80, "\n\n")

# Sample characteristics
cat("Sample Characteristics (N =", nrow(scf_young), "):\n\n")

# Age
cat("Age:\n")
cat(sprintf("  Mean: %.1f years (SD = %.1f)\n", 
            mean(scf_young$AGE, na.rm=T), 
            sd(scf_young$AGE, na.rm=T)))

# Marital status
cat("\nMarital Status:\n")
marriage_table <- table(scf_young$currently_married)
cat(sprintf("  Never married: %d (%.1f%%)\n", 
            marriage_table[1], 
            marriage_table[1]/sum(marriage_table)*100))
cat(sprintf("  Currently married: %d (%.1f%%)\n", 
            marriage_table[2], 
            marriage_table[2]/sum(marriage_table)*100))

# Education
cat("\nEducation:\n")
print(table(scf_young$educ_cat))

# Income
cat("\nIncome:\n")
cat(sprintf("  Median: $%s\n", 
            format(median(scf_young$INCOME, na.rm=T), big.mark=",", scientific=F)))
cat(sprintf("  Mean: $%s\n", 
            format(mean(scf_young$INCOME, na.rm=T), big.mark=",", scientific=F)))

# Debt
cat("\nDebt:\n")
cat(sprintf("  Median: $%s\n", 
            format(median(scf_young$DEBT, na.rm=T), big.mark=",", scientific=F)))
cat(sprintf("  Has any debt: %.1f%%\n", 
            mean(scf_young$DEBT > 0, na.rm=T)*100))

# Materialism index by marriage status
cat("\nMaterialism Index by Marriage Status:\n")
mat_by_marriage <- scf_young %>%
  group_by(currently_married) %>%
  summarise(
    n = n(),
    mean_materialism = mean(materialism_index, na.rm=T),
    sd_materialism = sd(materialism_index, na.rm=T)
  )

mat_by_marriage %>%
  mutate(
    married_status = ifelse(currently_married == 1, "Married", "Never Married")
  ) %>%
  select(married_status, n, mean_materialism, sd_materialism) %>%
  print()

# ========================================
# SAVE PREPARED DATA
# ========================================

cat("\n", "="*80, "\n")
cat("SAVING PREPARED DATA\n")
cat("="*80, "\n\n")

# Save as RData for subsequent analyses
save(scf_young, file = paste0(DATA_DIR, "scf_young_prepared.RData"))
cat("✓ Data saved to:", paste0(DATA_DIR, "scf_young_prepared.RData"), "\n")

# Save as CSV for portability
write_csv(scf_young, paste0(DATA_DIR, "scf_young_prepared.csv"))
cat("✓ CSV saved to:", paste0(DATA_DIR, "scf_young_prepared.csv"), "\n")

cat("\nData preparation complete!\n")
cat("Next: Run 02_SCF_Materialism_Index.R\n")
