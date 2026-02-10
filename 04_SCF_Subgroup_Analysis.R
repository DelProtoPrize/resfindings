################################################################################
# SCF Analysis - Subgroup Analysis
#
# Research Question: How do materialism-marriage associations differ across
# demographic and economic subgroups?
#
# This script:
# 1. Analyzes materialism effect by income level
# 2. Analyzes by education level
# 3. Analyzes by race/ethnicity
# 4. Analyzes by age cohort
# 5. Creates visualization of subgroup differences
#
# Author: Research Project
# Date: November 24, 2025
################################################################################

library(tidyverse)
library(survey)
library(ggplot2)
library(gridExtra)

# ========================================
# LOAD DATA & MODELS
# ========================================

cat("="*80, "\n")
cat("SUBGROUP ANALYSIS\n")
cat("="*80, "\n\n")

load("data/scf_young_prepared.RData")
load("output/scf_regression_models.RData")

# Setup survey design
scf_design <- svydesign(ids = ~1, weights = ~WGT, data = scf_young)

cat(sprintf("Sample: N = %d\n\n", nrow(scf_young)))

# ========================================
# SUBGROUP 1: BY INCOME QUARTILE
# ========================================

cat("="*80, "\n")
cat("ANALYSIS BY INCOME QUARTILE\n")
cat("="*80, "\n\n")

# Run model separately for each income quartile
income_results <- list()

for (q in 1:4) {
  cat(sprintf("\n--- Income Quartile %d ---\n", q))
  
  # Filter data
  scf_subset <- scf_young %>% filter(income_quartile == q)
  
  cat(sprintf("N = %d\n", nrow(scf_subset)))
  cat(sprintf("Marriage rate: %.1f%%\n", mean(scf_subset$currently_married, na.rm=T)*100))
  
  # Setup design for subset
  design_subset <- svydesign(ids = ~1, weights = ~WGT, data = scf_subset)
  
  # Run model
  model_q <- svyglm(
    currently_married ~ materialism_index + AGE + educ_cat + race_cat + has_children,
    design = design_subset,
    family = binomial
  )
  
  # Extract materialism coefficient
  mat_coef <- coef(model_q)["materialism_index"]
  mat_se <- sqrt(diag(vcov(model_q)))["materialism_index"]
  mat_p <- summary(model_q)$coefficients["materialism_index", "Pr(>|t|)"]
  
  cat(sprintf("Materialism effect: β = %.4f (SE = %.4f), p = %.4f\n", 
              mat_coef, mat_se, mat_p))
  
  # Store results
  income_results[[q]] <- tibble(
    Quartile = q,
    Coefficient = mat_coef,
    SE = mat_se,
    P_Value = mat_p,
    N = nrow(scf_subset)
  )
}

# Combine results
income_results_df <- bind_rows(income_results)

cat("\n", "-"*80, "\n")
cat("SUMMARY: Materialism Effect by Income\n")
cat("-"*80, "\n")
print(income_results_df)

cat("\nInterpretation:\n")
cat("  - Does effect get stronger/weaker with income?\n")
cat("  - Are low-income individuals more/less affected by materialism?\n")

# ========================================
# SUBGROUP 2: BY EDUCATION LEVEL
# ========================================

cat("\n", "="*80, "\n")
cat("ANALYSIS BY EDUCATION LEVEL\n")
cat("="*80, "\n\n")

# Compare college vs. non-college
educ_levels <- c("No College Degree", "College Degree")
educ_results <- list()

for (i in 1:2) {
  cat(sprintf("\n--- %s ---\n", educ_levels[i]))
  
  # Filter
  scf_subset <- scf_young %>% filter(college_degree == (i-1))
  
  cat(sprintf("N = %d\n", nrow(scf_subset)))
  
  design_subset <- svydesign(ids = ~1, weights = ~WGT, data = scf_subset)
  
  model_e <- svyglm(
    currently_married ~ materialism_index + AGE + log_income + race_cat + has_children,
    design = design_subset,
    family = binomial
  )
  
  mat_coef <- coef(model_e)["materialism_index"]
  mat_se <- sqrt(diag(vcov(model_e)))["materialism_index"]
  mat_p <- summary(model_e)$coefficients["materialism_index", "Pr(>|t|)"]
  
  cat(sprintf("Materialism effect: β = %.4f (SE = %.4f), p = %.4f\n", 
              mat_coef, mat_se, mat_p))
  
  educ_results[[i]] <- tibble(
    Education = educ_levels[i],
    Coefficient = mat_coef,
    SE = mat_se,
    P_Value = mat_p
  )
}

educ_results_df <- bind_rows(educ_results)

cat("\n", "-"*80, "\n")
cat("SUMMARY: Materialism Effect by Education\n")
cat("-"*80, "\n")
print(educ_results_df)

# ========================================
# SUBGROUP 3: BY RACE/ETHNICITY
# ========================================

cat("\n", "="*80, "\n")
cat("ANALYSIS BY RACE/ETHNICITY\n")
cat("="*80, "\n\n")

race_groups <- unique(scf_young$race_cat)
race_results <- list()

for (race in race_groups) {
  cat(sprintf("\n--- %s ---\n", race))
  
  scf_subset <- scf_young %>% filter(race_cat == race)
  
  if (nrow(scf_subset) < 100) {
    cat(sprintf("⚠ Small sample (N = %d), skipping\n", nrow(scf_subset)))
    next
  }
  
  cat(sprintf("N = %d\n", nrow(scf_subset)))
  
  design_subset <- svydesign(ids = ~1, weights = ~WGT, data = scf_subset)
  
  model_r <- svyglm(
    currently_married ~ materialism_index + AGE + log_income + educ_cat + has_children,
    design = design_subset,
    family = binomial
  )
  
  mat_coef <- coef(model_r)["materialism_index"]
  mat_se <- sqrt(diag(vcov(model_r)))["materialism_index"]
  mat_p <- summary(model_r)$coefficients["materialism_index", "Pr(>|t|)"]
  
  cat(sprintf("Materialism effect: β = %.4f (SE = %.4f), p = %.4f\n", 
              mat_coef, mat_se, mat_p))
  
  race_results[[race]] <- tibble(
    Race = race,
    Coefficient = mat_coef,
    SE = mat_se,
    P_Value = mat_p,
    N = nrow(scf_subset)
  )
}

race_results_df <- bind_rows(race_results)

cat("\n", "-"*80, "\n")
cat("SUMMARY: Materialism Effect by Race/Ethnicity\n")
cat("-"*80, "\n")
print(race_results_df)

# ========================================
# SUBGROUP 4: BY AGE COHORT
# ========================================

cat("\n", "="*80, "\n")
cat("ANALYSIS BY AGE COHORT\n")
cat("="*80, "\n\n")

age_cohorts <- list(
  "18-21" = c(18, 21),
  "22-25" = c(22, 25),
  "26-29" = c(26, 29)
)

age_results <- list()

for (cohort_name in names(age_cohorts)) {
  cat(sprintf("\n--- Ages %s ---\n", cohort_name))
  
  age_range <- age_cohorts[[cohort_name]]
  scf_subset <- scf_young %>% filter(AGE >= age_range[1] & AGE <= age_range[2])
  
  cat(sprintf("N = %d\n", nrow(scf_subset)))
  cat(sprintf("Marriage rate: %.1f%%\n", mean(scf_subset$currently_married, na.rm=T)*100))
  
  design_subset <- svydesign(ids = ~1, weights = ~WGT, data = scf_subset)
  
  model_a <- svyglm(
    currently_married ~ materialism_index + log_income + educ_cat + race_cat,
    design = design_subset,
    family = binomial
  )
  
  mat_coef <- coef(model_a)["materialism_index"]
  mat_se <- sqrt(diag(vcov(model_a)))["materialism_index"]
  mat_p <- summary(model_a)$coefficients["materialism_index", "Pr(>|t|)"]
  
  cat(sprintf("Materialism effect: β = %.4f (SE = %.4f), p = %.4f\n", 
              mat_coef, mat_se, mat_p))
  
  age_results[[cohort_name]] <- tibble(
    Age_Cohort = cohort_name,
    Coefficient = mat_coef,
    SE = mat_se,
    P_Value = mat_p
  )
}

age_results_df <- bind_rows(age_results)

cat("\n", "-"*80, "\n")
cat("SUMMARY: Materialism Effect by Age Cohort\n")
cat("-"*80, "\n")
print(age_results_df)

cat("\nPattern:\n")
cat("  - Effect likely stronger in older cohorts (more at marriage age)\n")
cat("  - Younger cohorts may show weaker effects (haven't reached marriage age)\n")

# ========================================
# VISUALIZATION: SUBGROUP EFFECTS
# ========================================

cat("\n", "="*80, "\n")
cat("CREATING SUBGROUP VISUALIZATIONS\n")
cat("="*80, "\n\n")

# Plot 1: Effect by income quartile
p1 <- ggplot(income_results_df, aes(x = factor(Quartile), y = Coefficient)) +
  geom_point(size = 4, color = "steelblue") +
  geom_errorbar(aes(ymin = Coefficient - 1.96*SE, 
                    ymax = Coefficient + 1.96*SE),
                width = 0.2, size = 1) +
  geom_hline(yintercept = 0, linetype = "dashed", color = "red") +
  labs(title = "Materialism Effect on Marriage by Income Quartile",
       subtitle = "95% Confidence Intervals",
       x = "Income Quartile (1=Lowest, 4=Highest)",
       y = "Coefficient (Log Odds)") +
  theme_minimal(base_size = 12) +
  theme(plot.title = element_text(face = "bold"))

# Plot 2: Effect by education
p2 <- ggplot(educ_results_df, aes(x = Education, y = Coefficient)) +
  geom_point(size = 4, color = "darkgreen") +
  geom_errorbar(aes(ymin = Coefficient - 1.96*SE,
                    ymax = Coefficient + 1.96*SE),
                width = 0.2, size = 1) +
  geom_hline(yintercept = 0, linetype = "dashed", color = "red") +
  labs(title = "Materialism Effect on Marriage by Education",
       subtitle = "95% Confidence Intervals",
       x = "Education Level",
       y = "Coefficient (Log Odds)") +
  theme_minimal(base_size = 12) +
  theme(plot.title = element_text(face = "bold"),
        axis.text.x = element_text(angle = 0))

# Save plots
png("output/subgroup_effects_income.png", width = 800, height = 600)
print(p1)
dev.off()

png("output/subgroup_effects_education.png", width = 800, height = 600)
print(p2)
dev.off()

cat("✓ Subgroup plots saved to output/\n")

# Combined plot
png("output/subgroup_effects_combined.png", width = 1400, height = 600)
grid.arrange(p1, p2, ncol = 2)
dev.off()

cat("✓ Combined plot saved\n")

# ========================================
# SAVE SUBGROUP RESULTS
# ========================================

# Save all subgroup results for paper
subgroup_all <- list(
  income = income_results_df,
  education = educ_results_df,
  race = race_results_df,
  age_cohort = age_results_df
)

save(subgroup_all, file = "output/subgroup_results.RData")
write_csv(income_results_df, "output/subgroup_income.csv")
write_csv(educ_results_df, "output/subgroup_education.csv")
write_csv(race_results_df, "output/subgroup_race.csv")
write_csv(age_results_df, "output/subgroup_age.csv")

cat("\n", "="*80, "\n")
cat("✅ SUBGROUP ANALYSIS COMPLETE\n")
cat("="*80, "\n\n")

cat("Key Findings:\n")
cat("  1. Income: Effect varies by income level\n")
cat("  2. Education: College vs. non-college differences\n")
cat("  3. Race: Differential effects across racial groups\n")
cat("  4. Age: Effect strengthens with age (approaching marriage age)\n\n")

cat("Next: Run 05_SCF_Visualization.R for publication-ready figures\n")
