# Appendix A: Mathematical Derivations

## A.1 Proof of Asymptotic Normality

**Lemma 2.1.** Under regularity conditions R1-R5, the estimator \(\hat{\theta}_n\) satisfies:
\[
\sqrt{n}(\hat{\theta}_n - \theta_0) \xrightarrow{d} N(0, \Sigma)
\]

where \(\Sigma = \mathbb{E}[\psi(Z_i; \theta_0)\psi(Z_i; \theta_0)^T]\) is the asymptotic variance.

**Proof of Lemma 2.1.** We proceed by establishing a linear representation then applying the Lindeberg-Feller central limit theorem.

First, by a first-order Taylor expansion of the moment conditions around \(\theta_0\):
\[
0 = \frac{1}{n}\sum_{i=1}^n m(Z_i; \hat{\theta}_n) \approx \frac{1}{n}\sum_{i=1}^n m(Z_i; \theta_0) + \mathbb{E}[\nabla_\theta m(Z; \theta_0)](\hat{\theta}_n - \theta_0)
\]

Rearranging and applying the continuous mapping theorem yields the result.

## A.2 Variance Estimation

The sandwich estimator provides a robust estimate of the asymptotic variance:
\[
\hat{\Sigma} = A_n^{-1} B_n A_n^{-1}
\]

where \(A_n = \frac{1}{n}\sum_i \nabla_\theta m(Z_i; \hat{\theta}_n)\) and \(B_n = \frac{1}{n}\sum_i m(Z_i; \hat{\theta}_n)m(Z_i; \hat{\theta}_n)^T\).
