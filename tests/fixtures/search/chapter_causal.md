# Chapter 1: Causal Inference Foundations

## 1.1 The Counterfactual Framework

The potential outcomes framework formalizes causality through counterfactuals. For each unit \(i\), we define \(Y_i(1)\) as the outcome under treatment and \(Y_i(0)\) as the outcome under control. The individual treatment effect is \(\tau_i = Y_i(1) - Y_i(0)\), but we can never observe both outcomes simultaneously.

### 1.1.1 Directed Acyclic Graphs

A Directed Acyclic Graph (DAG) encodes causal assumptions through directed edges. The DAG \(G = (V, E)\) where vertices represent variables and edges represent direct causal relationships. The absence of an edge implies no direct causal effect.

Given a DAG, the back-door criterion identifies sufficient adjustment sets. For treatment \(T\) and outcome \(Y\), a set \(Z\) satisfies the back-door criterion if no node in \(Z\) is a descendant of \(T\) and \(Z\) blocks every back-door path from \(T\) to \(Y\).

### 1.1.2 Regression Discontinuity Design

The regression discontinuity design exploits a known threshold or cutoff in the assignment variable. When units just above the cutoff receive treatment and those just below do not, the local average treatment effect can be identified at the boundary.

Formally, let \(X_i\) be the running variable with cutoff \(c\). Then:
\[
\tau_{RD} = \lim_{x \downarrow c} E[Y_i|X_i = x] - \lim_{x \uparrow c} E[Y_i|X_i = x]
\]

The key identifying assumption is continuity of the conditional expectation functions at the cutoff. Sharp RD designs have deterministic treatment assignment: \(T_i = 1[X_i \ge c]\).
