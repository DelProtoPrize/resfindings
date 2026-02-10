################################################################################
# SCF Analysis - Materialism Index Construction & Validation
#
# This script:
# 1. Creates materialism index from SCF behavioral proxies
# 2. Validates index (internal consistency, correlations)
# 3. Compares alternative index specifications
# 4. Generates descriptive tables
#
# Author: Research Project
# Date: November 24, 2025
################################################################################

library(tidyverse)
library(psych)  # For alpha coefficient
library(corrplot)  # For correlation plots
library(stargazer)

# ========================================
# LOAD PREPARED DATA
# ========================================

cat("="*80, "\n")
cat("MATERIALISM INDEX CONSTRUCTION\n")
cat("="*80, "\n\n")

load("data/scf_young_prepared.RData")

cat(sprintf("Data loaded: N = %d young adults (18-29)\n\n", nrow(scf_young)))

# ========================================
# APPROACH 1: COMPOSITE INDEX (Recommended)
# ========================================

cat("="*80, "\n")
cat("APPROACH 1: COMPOSITE MATERIALISM INDEX\n")
cat("="*80, "\n\n")

cat("Components:\n")
cat("  1. Credit card balance (CCBAL)\n")
cat("  2. Installment debt (INSTALL) - auto loans, furniture, appliances\n")
cat("  3. Number of vehicles (VEHIC)\n")
cat("  4. Debt-to-income ratio\n\n")

# Check distributions (before standardization)
cat("Raw Component Distributions:\n")
cat(sprintf("  Credit card balance: Median = $%s, Mean = $%s\n",
            format(median(scf_young$CCBAL, na.rm=T), big.mark=","),
            format(mean(scf_young$CCBAL, na.rm=T), big.mark=",")))
cat(sprintf("  Installment debt: Median = $%s, Mean = $%s\n",
            format(median(scf_young$INSTALL, na.rm=T), big.mark=","),
            format(mean(scf_young$INSTALL, na.rm=T), big.mark=",")))
cat(sprintf("  Vehicles: Median = %.1f, Mean = %.2f\n",
            median(scf_young$VEHIC, na.rm=T),
            mean(scf_young$VEHIC, na.rm=T)))
cat(sprintf("  Debt-to-income: Median = %.2f, Mean = %.2f\n\n",
            median(scf_young$debt_to_income, na.rm=T),
            mean(scf_young$debt_to_income, na.rm=T)))

# Correlation matrix (check if components measure same construct)
cat("Correlation Matrix of Components:\n")
materialism_components <- scf_young %>%
  select(z_ccbal, z_install, z_vehic, z_debt_income) %>%
  na.omit()

cor_matrix <- cor(materialism_components)
print(round(cor_matrix, 3))

# Visualize correlations
png("output/materialism_component_correlations.png", width=800, height=600)
corrplot(cor_matrix, method="color", type="upper",
         addCoef.col = "black", number.cex=1.2,
         tl.col="black", tl.srt=45,
         title="Materialism Component Correlations",
         mar=c(0,0,2,0))
dev.off()
cat("\n✓ Correlation plot saved to: output/materialism_component_correlations.png\n")

# Internal consistency (Cronbach's alpha)
cat("\nInternal Consistency (Cronbach's Alpha):\n")
alpha_result <- alpha(materialism_components)
cat(sprintf("  Alpha = %.3f\n", alpha_result$total$raw_alpha))

if (alpha_result$total$raw_alpha >= 0.70) {
  cat("  ✓ GOOD: Alpha ≥ 0.70 indicates acceptable internal consistency\n")
} else if (alpha_result$total$raw_alpha >= 0.60) {
  cat("  ⚠ ACCEPTABLE: Alpha ≥ 0.60 is marginally acceptable\n")
} else {
  cat("  ✗ POOR: Alpha < 0.60 suggests components don't measure same construct\n")
  cat("  → Consider removing poorly-correlating components\n")
}

# ========================================
# MATERIALISM INDEX DISTRIBUTION
# ========================================

cat("\n", "="*80, "\n")
cat("MATERIALISM INDEX DISTRIBUTION\n")
cat("="*80, "\n\n")

summary_stats <- scf_young %>%
  summarise(
    N = sum(!is.na(materialism_index)),
    Mean = mean(materialism_index, na.rm=T),
    SD = sd(materialism_index, na.rm=T),
    Min = min(materialism_index, na.rm=T),
    Q25 = quantile(materialism_index, 0.25, na.rm=T),
    Median = median(materialism_index, na.rm=T),
    Q75 = quantile(materialism_index, 0.75, na.rm=T),
    Max = max(materialism_index, na.rm=T),
    Skewness = psych::skew(materialism_index, na.rm=T)
  )

print(round(summary_stats, 3))

# Plot distribution
png("output/materialism_index_distribution.png", width=1000, height=600)
par(mfrow=c(1,2))

# Histogram
hist(scf_young$materialism_index, 
     breaks=50, 
     col="steelblue",
     border="white",
     main="Materialism Index Distribution",
     xlab="Materialism Index (Standardized)",
     ylab="Frequency")
abline(v=mean(scf_young$materialism_index, na.rm=T), 
       col="red", lwd=2, lty=2)
legend("topright", "Mean", col="red", lty=2, lwd=2)

# Q-Q plot (check normality)
qqnorm(scf_young$materialism_index, main="Q-Q Plot: Materialism Index")
qqline(scf_young$materialism_index, col="red", lwd=2)

dev.off()
cat("\n✓ Distribution plots saved to: output/materialism_index_distribution.png\n")

# ========================================
# MATERIALISM BY DEMOGRAPHICS
# ========================================

cat("\n", "="*80, "\n")
cat("MATERIALISM INDEX BY DEMOGRAPHIC GROUPS\n")
cat("="*80, "\n\n")

# By marriage status
cat("By Marriage Status:\n")
scf_young %>%
  group_by(currently_married) %>%
  summarise(
    N = n(),
    Mean_Materialism = mean(materialism_index, na.rm=T),
    SD = sd(materialism_index, na.rm=T),
    Married_Status = ifelse(first(currently_married)==1, "Married", "Never Married")
  ) %>%
  select(Married_Status, N, Mean_Materialism, SD) %>%
  print()

# Test difference
t_test_result <- t.test(materialism_index ~ currently_married, data = scf_young)
cat(sprintf("\nT-test: t = %.3f, p = %.4f\n", 
            t_test_result$statistic, 
            t_test_result$p.value))

if (t_test_result$p.value < 0.05) {
  cat("✓ SIGNIFICANT: Married and never-married differ on materialism\n")
} else {
  cat("⚠ NOT SIGNIFICANT: No difference by marriage status\n")
}

# By income quartile
cat("\nBy Income Quartile:\n")
scf_young %>%
  group_by(income_quartile) %>%
  summarise(
    N = n(),
    Mean_Materialism = mean(materialism_index, na.rm=T),
    SD = sd(materialism_index, na.rm=T)
  ) %>%
  print()

# By education
cat("\nBy Education Level:\n")
scf_young %>%
  group_by(educ_cat) %>%
  summarise(
    N = n(),
    Mean_Materialism = mean(materialism_index, na.rm=T),
    SD = sd(materialism_index, na.rm=T)
  ) %>%
  print()

# ========================================
# ALTERNATIVE INDEX SPECIFICATIONS
# ========================================

cat("\n", "="*80, "\n")
cat("ALTERNATIVE MATERIALISM INDEX SPECIFICATIONS\n")
cat("="*80, "\n\n")

scf_young <- scf_young %>%
  mutate(
    # Alternative 1: Consumer debt only (no vehicles)
    materialism_debt_only = (z_ccbal + z_install + z_debt_income) / 3,
    
    # Alternative 2: Possessions only (vehicles, later can add housing)
    materialism_possessions = z_vehic,
    
    # Alternative 3: Weighted index (debt weighted more heavily)
    materialism_weighted = (2*z_ccbal + 2*z_install + z_vehic + z_debt_income) / 6,
    
    # Alternative 4: Binary high materialism (top 25%)
    materialism_top_quartile = ifelse(materialism_index > quantile(materialism_index, 0.75, na.rm=T), 1, 0)
  )

cat("Created 4 alternative materialism specifications:\n")
cat("  1. Main index (4 components, equal weight)\n")
cat("  2. Debt-only index (3 components)\n")
cat("  3. Possessions index (vehicles only)\n")
cat("  4. Weighted index (debt weighted 2x)\n")
cat("  5. Binary top quartile\n")

# Correlations between alternative specifications
cat("\nCorrelations Between Alternative Specifications:\n")
alt_cors <- scf_young %>%
  select(materialism_index, materialism_debt_only, 
         materialism_possessions, materialism_weighted) %>%
  cor(use = "complete.obs")

print(round(alt_cors, 3))

# ========================================
# CONSTRUCT VALIDITY EVIDENCE
# ========================================

cat("\n", "="*80, "\n")
cat("CONSTRUCT VALIDITY EVIDENCE\n")
cat("="*80, "\n\n")

cat("Testing if materialism index correlates with expected behaviors:\n\n")

# Should correlate with:
# 1. Higher spending (if available)
# 2. Lower savings
# 3. More debt
# 4. Lower net worth (spending > saving)

validity_cors <- scf_young %>%
  select(materialism_index, DEBT, INCOME, NETWORTH, CCBAL) %>%
  cor(use = "complete.obs")

cat("Correlations with Key Financial Variables:\n")
cat(sprintf("  Materialism × Total Debt: r = %.3f\n", validity_cors["materialism_index", "DEBT"]))
cat(sprintf("  Materialism × Income: r = %.3f\n", validity_cors["materialism_index", "INCOME"]))
cat(sprintf("  Materialism × Net Worth: r = %.3f\n", validity_cors["materialism_index", "NETWORTH"]))
cat(sprintf("  Materialism × Credit Card Debt: r = %.3f\n", validity_cors["materialism_index", "CCBAL"]))

cat("\nExpected Patterns:\n")
cat("  ✓ Positive correlation with debt (spending for possessions)\n")
cat("  ✓ Negative correlation with net worth (consuming rather than saving)\n")
cat("  ✓ Weak/no correlation with income (materialism is about values, not ability)\n")

# ========================================
# SAVE RESULTS
# ========================================

# Save correlation matrices for appendix
write_csv(as.data.frame(cor_matrix), "output/materialism_component_correlations.csv")
write_csv(as.data.frame(validity_cors), "output/construct_validity_correlations.csv")

cat("\n", "="*80, "\n")
cat("✅ MATERIALISM INDEX CONSTRUCTION COMPLETE\n")
cat("="*80, "\n\n")

cat("Key Outputs:\n")
cat("  1. materialism_index (main measure)\n")
cat("  2. materialism_tertile (categorical, for descriptives)\n")
cat("  3. materialism_high (binary, for logistic models)\n")
cat("  4. Alternative specifications for robustness checks\n\n")

cat("Next: Run 03_SCF_Main_Analysis.R\n")
