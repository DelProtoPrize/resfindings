# Introduction

1.  Introduction The relationship between economic resources and
    fertility is one of the most debated topics in modern demography.
    Classical economic theory often views children as “normal goods,”
    suggesting that as household resources increase, the demand for
    children should also rise. However, recent microeconomic literature
    reveals a more complex, divergent reality in which the impact of
    wealth depends heavily on housing tenure and age.

1.1 Theoretical Framework: The Wealth Paradox Research indicates that
for homeowners, rising property values create a “wealth shock” that acts
as a financial safety net, increasing the probability of having a child
by 16% to 18%. In contrast, for non-owners and adolescents, rising
housing costs act as a barrier. Specifically, increased parental housing
wealth has been associated with an 18% reduction in the likelihood of
teenage birth, suggesting that economic stability improves long-term
opportunities that discourage early childbearing.

1.2 Objective of Analysis While these micro-level studies focus on
individual household wealth, this analysis seeks to understand if these
patterns hold at a global, macro-economic level. Using data from the
World Development Indicators (WDI), we examine whether Urbanization—a
proxy for economic development and higher living costs—mirrors the
“divergence” seen in microdata.

1.3 Key Variables & Hypothesis We utilize 2022 WDI data to explore three
key variables:

Urban Population (%): Our primary independent variable, representing the
shift from agrarian to developed economies.

Adolescent Fertility Rate: To test the hypothesis that development acts
as a contraceptive for teenagers.

Total Fertility Rate: To observe the aggregate impact of urbanization on
family size.

We hypothesize a negative correlation between urbanization and teen
pregnancy, consistent with the “opportunity cost” theory observed in the
literature. However, the relationship with total fertility remains an
open question: does the “wealth shock” of developed nations outweigh the
rising costs of urban living?

## Methods

## Data

    ## # A tibble: 217 × 82
    ##    d           cname_s cleanfuel_pop electric_pop agedep agedep_old agedep_young
    ##    <chr>       <chr>           <dbl>        <dbl>  <dbl>      <dbl>        <dbl>
    ##  1 Afghanistan AFG              37.1         85.3   85.0       4.36         80.6
    ##  2 Albania     ALB              85.4        100     49.3      23.5          25.7
    ##  3 Algeria     DZA              99.7        100     58.8       9.82         49.0
    ##  4 American S… ASM              NA           NA     54.3      11.0          43.3
    ##  5 Andorra     AND             100          100     38.2      20.7          17.5
    ##  6 Angola      AGO              50           48.5   90.6       5.29         85.3
    ##  7 Antigua an… ATG             100          100     41.4      15.2          26.3
    ##  8 Argentina   ARG              99.9        100     53.3      18.5          34.9
    ##  9 Armenia     ARM              98.7        100     48.8      19.2          29.5
    ## 10 Aruba       ABW              NA           99.9   49.8      23.5          26.3
    ## # ℹ 207 more rows
    ## # ℹ 75 more variables: consume_poor <dbl>, socins_pop <dbl>, socins_poor <dbl>,
    ## #   safetynet_pop <dbl>, safetynet_poor <dbl>, unempins_pop <dbl>,
    ## #   unempins_poor <dbl>, diabetes <lgl>, university <dbl>,
    ## #   university_men <dbl>, university_female <dbl>, secondary <dbl>,
    ## #   secondary_men <dbl>, secondary_female <dbl>, postsec <dbl>,
    ## #   postsec_men <dbl>, postsec_fem <dbl>, hiv_y <dbl>, hiv_a <dbl>, …

    ## # A tibble: 6 × 82
    ##   d            cname_s cleanfuel_pop electric_pop agedep agedep_old agedep_young
    ##   <chr>        <chr>           <dbl>        <dbl>  <dbl>      <dbl>        <dbl>
    ## 1 Afghanistan  AFG              37.1         85.3   85.0       4.36         80.6
    ## 2 Albania      ALB              85.4        100     49.3      23.5          25.7
    ## 3 Algeria      DZA              99.7        100     58.8       9.82         49.0
    ## 4 American Sa… ASM              NA           NA     54.3      11.0          43.3
    ## 5 Andorra      AND             100          100     38.2      20.7          17.5
    ## 6 Angola       AGO              50           48.5   90.6       5.29         85.3
    ## # ℹ 75 more variables: consume_poor <dbl>, socins_pop <dbl>, socins_poor <dbl>,
    ## #   safetynet_pop <dbl>, safetynet_poor <dbl>, unempins_pop <dbl>,
    ## #   unempins_poor <dbl>, diabetes <lgl>, university <dbl>,
    ## #   university_men <dbl>, university_female <dbl>, secondary <dbl>,
    ## #   secondary_men <dbl>, secondary_female <dbl>, postsec <dbl>,
    ## #   postsec_men <dbl>, postsec_fem <dbl>, hiv_y <dbl>, hiv_a <dbl>, hiv <dbl>,
    ## #   malaria <dbl>, internet <dbl>, migrants <lgl>, laborp_fem <dbl>, …

    ##    Min. 1st Qu.  Median    Mean 3rd Qu.    Max. 
    ##    4.40    9.90   14.12   17.81   25.16   45.42

    ## Warning: Unknown or uninitialised column: `birth_rate`.

    ## < table of extent 0 >

    ## 
    ## Attaching package: 'dplyr'

    ## The following objects are masked from 'package:stats':
    ## 
    ##     filter, lag

    ## The following objects are masked from 'package:base':
    ## 
    ##     intersect, setdiff, setequal, union

    ## === Statistical Comparison Results ===

    ## 
    ## Analyzing: births 
    ## 
    ## Analyzing: teenpreg 
    ## 
    ## Analyzing: death

# — Summary Table—

    ## # A tibble: 2 × 5
    ##   urban_category    Avg_Births Avg_Teen_Pregnancy Avg_Death_Rate Count_Countries
    ##   <chr>                  <dbl>              <dbl>          <dbl>           <int>
    ## 1 Bottom 20% (Low …       26.0               65.0           7.20              44
    ## 2 Top 20% (High Ur…       12.2               17.0           7.33              44

# Empirics

    ##    Min. 1st Qu.  Median    Mean 3rd Qu.    Max. 
    ##    4.40    9.90   14.12   17.81   25.16   45.42

    ## 
    ## FALSE 
    ##   217

<table style="width:100%;">
<caption>Averages by Urbanization Level</caption>
<colgroup>
<col style="width: 29%" />
<col style="width: 13%" />
<col style="width: 17%" />
<col style="width: 18%" />
<col style="width: 20%" />
</colgroup>
<thead>
<tr>
<th style="text-align: left;">urban_category</th>
<th style="text-align: right;">Avg_Births</th>
<th style="text-align: right;">Avg_Teen_Preg</th>
<th style="text-align: right;">Avg_Death_Rate</th>
<th style="text-align: right;">Count_Countries</th>
</tr>
</thead>
<tbody>
<tr>
<td style="text-align: left;">Bottom 20% (Low Urban)</td>
<td style="text-align: right;">26.02002</td>
<td style="text-align: right;">64.96523</td>
<td style="text-align: right;">7.199023</td>
<td style="text-align: right;">44</td>
</tr>
<tr>
<td style="text-align: left;">Top 20% (High Urban)</td>
<td style="text-align: right;">12.22132</td>
<td style="text-align: right;">16.98443</td>
<td style="text-align: right;">7.330204</td>
<td style="text-align: right;">44</td>
</tr>
</tbody>
</table>

## Statistical Analysis (T-Tests)

    ## 
    ## ==============================
    ## TESTING VARIABLE: births 
    ## 
    ##  Welch Two Sample t-test
    ## 
    ## data:  analysis_data[[var]] by analysis_data$urban_category
    ## t = 8.4537, df = 70.248, p-value = 2.601e-12
    ## alternative hypothesis: true difference in means between group Bottom 20% (Low Urban) and group Top 20% (High Urban) is not equal to 0
    ## 95 percent confidence interval:
    ##  10.54345 17.05396
    ## sample estimates:
    ## mean in group Bottom 20% (Low Urban)   mean in group Top 20% (High Urban) 
    ##                             26.02002                             12.22132 
    ## 
    ## 
    ## ==============================
    ## TESTING VARIABLE: teenpreg 
    ## 
    ##  Welch Two Sample t-test
    ## 
    ## data:  analysis_data[[var]] by analysis_data$urban_category
    ## t = 7.0411, df = 65.191, p-value = 1.448e-09
    ## alternative hypothesis: true difference in means between group Bottom 20% (Low Urban) and group Top 20% (High Urban) is not equal to 0
    ## 95 percent confidence interval:
    ##  34.37233 61.58926
    ## sample estimates:
    ## mean in group Bottom 20% (Low Urban)   mean in group Top 20% (High Urban) 
    ##                             64.96523                             16.98443 
    ## 
    ## 
    ## ==============================
    ## TESTING VARIABLE: death 
    ## 
    ##  Welch Two Sample t-test
    ## 
    ## data:  analysis_data[[var]] by analysis_data$urban_category
    ## t = -0.21437, df = 60.748, p-value = 0.831
    ## alternative hypothesis: true difference in means between group Bottom 20% (Low Urban) and group Top 20% (High Urban) is not equal to 0
    ## 95 percent confidence interval:
    ##  -1.354904  1.092541
    ## sample estimates:
    ## mean in group Bottom 20% (Low Urban)   mean in group Top 20% (High Urban) 
    ##                             7.199023                             7.330204

## Visual Bivariate Analysis

You can also embed plots, for example:

![](WDI-Analysis_files/figure-markdown_strict/pressure-1.png)

## The plots provide strong empirical evidence supporting the “Divergence” theory from our research notes. Specifically, they illustrate how urbanization (a proxy for economic development and housing cost) acts as a contraceptive force.

    ## Warning: package 'ggpubr' was built under R version 4.5.2

    ## `geom_smooth()` using formula = 'y ~ x'
    ## `geom_smooth()` using formula = 'y ~ x'
    ## `geom_smooth()` using formula = 'y ~ x'

![](WDI-Analysis_files/figure-markdown_strict/unnamed-chunk-5-1.png)

### Urbanization vs. Birth Rate (Top Plot) The Result: A moderate negative correlation (R=-0.53). Significance: The P-value (p&lt;2.2e-16) is extremely small, indicating that this relationship is not random and may be statistically significant. Interpretation: As countries become more urbanized (moving from 25% to 75% urban), the average birth rate drops consistently.Connection to your Research: This aligns with the “Renter” hypothesis. In highly urbanized environments, housing costs are typically higher, and space is more constrained. Unlike rural agrarian settings, where children are economic assets (labor), in urban settings, they become “economic liabilities” (high cost of raising). The “substitution effect,” in which high housing costs compete with child-rearing costs, is evident here on a global scale.

### Urbanization vs. Teen Pregnancy (Middle Plot)The Result: A moderate negative correlation (R=-0.47).Significance: Also highly significant (p=3.1e-13).Interpretation: As urbanization increases, teen pregnancy decreases.Connection to your Research: This strongly supports your “Teen Divergence” note ($100k increase in wealth is associated with an 18% decline in teen births). Urban areas usually offer better educational and economic opportunities for women. The “opportunity cost” of having a child as a teenager in a city is much higher than in a rural area, leading to delayed childbearing.

### Birth Rate vs. Teen Pregnancy (Bottom Plot) The Result: A very strong positive correlation (R=0.86). Interpretation: Countries with high overall birth rates almost always have high teen pregnancy rates. Why this matters: This suggests that “Teen Pregnancy” is a major driver of “Total Fertility.” If you want to lower the total birth rate, you must address the factors driving teen pregnancy (likely lack of opportunity/wealth). Anomaly Note: Notice the green dots clustering tightly at the low end (bottom left). This is the “Developed World” cluster, characterized by low total fertility and near-zero rates of adolescent pregnancy. This is likely where your “Homeowner Wealth Shock” effect would be hidden, within this specific cluster. Wealthy homeowners might be having slightly more kids, but they are statistically drowned out by the massive global trend of “Development = Fewer Kids.”

### **Summary** The bivariate analysis supports the idea that urbanization is a “fertility depressant.” It acts as a barrier to fertility for the general population (renters/average citizens) and a deterrent for teenagers, overpowering any “wealth shock” fertility boost that might exist for a small subset of wealthy homeowners.
