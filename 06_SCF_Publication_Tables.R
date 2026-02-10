################################################################################
# SCF Analysis - Publication-Ready Tables
#
# This script creates:
# 1. Table 1: Descriptive statistics
# 2. Table 2: Main regression results
# 3. Table 3: Subgroup analysis summary
# 4. Table 4: Robustness checks (alternative specifications)
#
# Outputs: LaTeX format for journal submission + CSV for reference
#
# Author: Research Project
# Date: November 24, 2025
################################################################################

library(tidyverse)
library(survey)
library(stargazer)
library(knitr)
library(kableExtra)

# Load data and models
load("data/scf_young_prepared.RData")
load("output/scf_regression_models.RData")
load("output/subgroup_results.RData")

cat("="*80, "\n")
cat("CREATING PUBLICATION TABLES\n")
cat("="*80, "\n\n")

# ========================================
# TABLE 1: DESCRIPTIVE STATISTICS
# ========================================

cat("Creating Table 1: Descriptive Statistics...\n")

# Calculate weighted means and SDs
desc_stats <- scf_young %>%
  summarise(
    across(c(AGE, INCOME, DEBT, CCBAL, INSTALL, VEHIC, 
             debt_to_income, materialism_index),
           list(
             Mean = ~weighted.mean(., WGT, na.rm = TRUE),
             SD = ~sqrt(weighted.mean((. - weighted.mean(., WGT, na.rm=T))^2, WGT, na.rm=T))
           ),
           .names = "{col}_{fn}")
  ) %>%
  pivot_longer(cols = everything(),
               names_to = c("Variable", ".value"),
               names_pattern = "(.*)_(Mean|SD)") %>%
  mutate(
    Variable = case_when(
      Variable == "AGE" ~ "Age (years)",
      Variable == "INCOME" ~ "Household Income ($)",
      Variable == "DEBT" ~ "Total Debt ($)",
      Variable == "CCBAL" ~ "Credit Card Balance ($)",
      Variable == "INSTALL" ~ "Installment Debt ($)",
      Variable == "VEHIC" ~ "Number of Vehicles",
      Variable == "debt_to_income" ~ "Debt-to-Income Ratio",
      Variable == "materialism_index" ~ "Materialism Index",
      TRUE ~ Variable
    )
  )

# Add categorical variables
cat_stats <- tibble(
  Variable = c("Currently Married (%)", 
               "Has Children (%)",
               "College Degree (%)",
               "White (%)",
               "Black (%)",
               "Hispanic (%)"),
  Mean = c(
    weighted.mean(scf_young$currently_married, scf_young$WGT, na.rm=T) * 100,
    weighted.mean(scf_young$has_children, scf_young$WGT, na.rm=T) * 100,
    weighted.mean(scf_young$college_degree, scf_young$WGT, na.rm=T) * 100,
    weighted.mean(scf_young$race_cat == "White", scf_young$WGT, na.rm=T) * 100,
    weighted.mean(scf_young$race_cat == "Black", scf_young$WGT, na.rm=T) * 100,
    weighted.mean(scf_young$race_cat == "Hispanic", scf_young$WGT, na.rm=T) * 100
  ),
  SD = NA_real_
)

# Combine
table1 <- bind_rows(desc_stats, cat_stats)

# Format for publication
table1_formatted <- table1 %>%
  mutate(
    Mean = ifelse(grepl("\\$", Variable),
                  paste0("$", format(round(Mean, 0), big.mark = ",")),
                  sprintf("%.2f", Mean)),
    SD = ifelse(!is.na(SD) & !grepl("%", Variable),
                sprintf("(%.2f)", SD),
                "")
  )

# Save as CSV
write_csv(table1_formatted, "output/Table_1_Descriptive_Statistics.csv")

# Print LaTeX version
cat("\nTable 1: Descriptive Statistics\n")
cat("(LaTeX format for journal submission)\n\n")

# Create LaTeX table
latex_table1 <- kable(table1_formatted, 
                      format = "latex",
                      booktabs = TRUE,
                      caption = "Descriptive Statistics: Young Adults (Ages 18-29), Survey of Consumer Finances 2019-2022",
                      col.names = c("Variable", "Mean/Percent", "SD"))

writeLines(latex_table1, "output/Table_1_Descriptive_Statistics.tex")

cat("✓ Table 1 saved:\n")
cat("    CSV: output/Table_1_Descriptive_Statistics.csv\n")
cat("    LaTeX: output/Table_1_Descriptive_Statistics.tex\n")

# ========================================
# TABLE 2: MAIN REGRESSION RESULTS
# ========================================

cat("\nCreating Table 2: Main Regression Results...\n")

# Extract results from models
create_reg_table <- function(models, model_names) {
  
  results_list <- list()
  
  for (i in seq_along(models)) {
    model <- models[[i]]
    coefs <- summary(model)$coefficients
    
    # Key variables to report
    key_vars <- c("materialism_index", "AGE", "log_income", 
                  "college_degree", "has_children")
    
    # Filter for key variables
    coefs_filtered <- coefs[rownames(coefs) %in% key_vars, ]
    
    results_list[[i]] <- tibble(
      Variable = rownames(coefs_filtered),
      Estimate = coefs_filtered[, "Estimate"],
      SE = coefs_filtered[, "Std. Error"],
      P_Value = coefs_filtered[, "Pr(>|t|)"],
      Model = model_names[i]
    )
  }
  
  bind_rows(results_list)
}

# Create table
reg_results <- create_reg_table(
  list(model_1, model_2, model_5),
  c("Currently Married", "Married by 25", "Comprehensive")
)

# Pivot for side-by-side comparison
table2 <- reg_results %>%
  mutate(
    Estimate_SE = sprintf("%.4f\n(%.4f)%s",
                          Estimate, SE,
                          case_when(
                            P_Value < 0.001 ~ "***",
                            P_Value < 0.01 ~ "**",
                            P_Value < 0.05 ~ "*",
                            TRUE ~ ""
                          ))
  ) %>%
  select(Variable, Model, Estimate_SE) %>%
  pivot_wider(names_from = Model, values_from = Estimate_SE)

# Save
write_csv(table2, "output/Table_2_Regression_Results.csv")

cat("✓ Table 2 saved: output/Table_2_Regression_Results.csv\n")

# ========================================
# TABLE 3: SUBGROUP EFFECTS SUMMARY
# ========================================

cat("\nCreating Table 3: Subgroup Effects...\n")

# Create comprehensive subgroup table
table3 <- bind_rows(
  income_results_df %>% 
    mutate(Subgroup_Category = "Income Quartile") %>%
    rename(Subgroup = Quartile) %>%
    mutate(Subgroup = paste("Quartile", Subgroup)),
  
  educ_results_df %>%
    mutate(Subgroup_Category = "Education") %>%
    rename(Subgroup = Education),
  
  age_results_df %>%
    mutate(Subgroup_Category = "Age Cohort") %>%
    rename(Subgroup = Age_Cohort)
) %>%
  mutate(
    Coefficient_Formatted = sprintf("%.4f", Coefficient),
    SE_Formatted = sprintf("(%.4f)", SE),
    Significance = case_when(
      P_Value < 0.001 ~ "***",
      P_Value < 0.01 ~ "**",
      P_Value < 0.05 ~ "*",
      TRUE ~ ""
    ),
    Result = paste0(Coefficient_Formatted, Significance, "\n", SE_Formatted)
  ) %>%
  select(Subgroup_Category, Subgroup, Result)

write_csv(table3, "output/Table_3_Subgroup_Effects.csv")

cat("✓ Table 3 saved: output/Table_3_Subgroup_Effects.csv\n")

# ========================================
# TABLE 4: ROBUSTNESS CHECKS
# ========================================

cat("\nCreating Table 4: Robustness Checks...\n")

cat("Testing alternative materialism index specifications:\n")
cat("  1. Main index (4 components)\n")
cat("  2. Debt-only index\n")
cat("  3. Weighted index (debt emphasized)\n")
cat("  4. Binary high materialism\n\n")

# Run models with alternative specifications
scf_design <- svydesign(ids = ~1, weights = ~WGT, data = scf_young)

# Main specification (already have as model_1)

# Alternative 1: Debt-only index
model_robust_1 <- svyglm(
  currently_married ~ materialism_debt_only + 
                      AGE + log_income + educ_cat + race_cat + has_children,
  design = scf_design,
  family = binomial
)

# Alternative 2: Weighted index
model_robust_2 <- svyglm(
  currently_married ~ materialism_weighted +
                      AGE + log_income + educ_cat + race_cat + has_children,
  design = scf_design,
  family = binomial
)

# Alternative 3: Binary high materialism
model_robust_3 <- svyglm(
  currently_married ~ materialism_top_quartile +
                      AGE + log_income + educ_cat + race_cat + has_children,
  design = scf_design,
  family = binomial
)

# Extract materialism coefficients
robustness_results <- tibble(
  Specification = c("Main Index (4 components)",
                   "Debt-Only Index",
                   "Weighted Index (debt 2x)",
                   "Binary Top Quartile"),
  Coefficient = c(
    coef(model_1)["materialism_index"],
    coef(model_robust_1)["materialism_debt_only"],
    coef(model_robust_2)["materialism_weighted"],
    coef(model_robust_3)["materialism_top_quartile"]
  ),
  SE = c(
    sqrt(diag(vcov(model_1)))["materialism_index"],
    sqrt(diag(vcov(model_robust_1)))["materialism_debt_only"],
    sqrt(diag(vcov(model_robust_2)))["materialism_weighted"],
    sqrt(diag(vcov(model_robust_3)))["materialism_top_quartile"]
  )
) %>%
  mutate(
    P_Value = 2 * pnorm(-abs(Coefficient / SE)),
    Sig = case_when(
      P_Value < 0.001 ~ "***",
      P_Value < 0.01 ~ "**",
      P_Value < 0.05 ~ "*",
      TRUE ~ ""
    ),
    Result = sprintf("%.4f%s\n(%.4f)", Coefficient, Sig, SE)
  )

write_csv(robustness_results, "output/Table_4_Robustness_Checks.csv")

cat("Robustness Check Results:\n")
print(robustness_results %>% select(Specification, Coefficient, SE, Sig))

cat("\n✓ Table 4 saved: output/Table_4_Robustness_Checks.csv\n")

# ========================================
# COMPREHENSIVE SUMMARY
# ========================================

cat("\n", "="*80, "\n")
cat("✅ ALL PUBLICATION TABLES COMPLETE\n")
cat("="*80, "\n\n")

cat("Tables Created:\n")
cat("  1. Descriptive Statistics (CSV + LaTeX)\n")
cat("  2. Main Regression Results (CSV)\n")
cat("  3. Subgroup Effects (CSV)\n")
cat("  4. Robustness Checks (CSV)\n\n")

cat("All tables saved to: output/\n\n")

cat("For Your Paper:\n")
cat("  - Include Tables 1-4 in main text\n")
cat("  - Include Figures 1-5 as exhibits\n")
cat("  - Full model output available in .RData files\n\n")

cat("✅ SCF analysis pipeline complete!\n")
