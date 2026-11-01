# Chapter 2: Panel Data Methods

## 2.1 Difference-in-Differences

Difference-in-differences (DiD) compares the change in outcomes over time for treated and untreated groups. The canonical two-period, two-group setup estimates:

\[
\tau_{DiD} = (E[Y_{i1}|T_i = 1] - E[Y_{i0}|T_i = 1]) - (E[Y_{i1}|T_i = 0] - E[Y_{i0}|T_i = 0])
\]

The key identifying assumption is parallel trends: in the absence of treatment, the average outcomes for treated and untreated groups would have followed parallel paths over time.

### 2.1.1 Recent Extensions

Recent developments include staggered adoption designs, event study specifications, and methods robust to heterogeneous treatment effects. The Goodman-Bacon decomposition reveals that two-way fixed effects estimators produce weighted averages of treatment effects across timing groups.

## 2.2 Synthetic Control Method

The synthetic control method constructs a weighted combination of untreated units that approximates the pre-treatment trajectory of the treated unit. The weights are chosen to minimize pre-treatment prediction error.

For a treated unit with pre-treatment covariates \(X_1\) and outcomes \(Y_1\), the synthetic control weights \(W^*\) solve:
\[
W^* = \argmin_W \|X_1 - X_0 W\|
\]

The treatment effect at time \(t\) is then \(\hat{\tau}_t = Y_{1t} - \sum_{j} W_j^* Y_{jt}\).
