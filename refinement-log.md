# Deliberative Refinement — Statistical Tracking

## Methodology

Each cycle: 10 councils × V(10,3,1) = 10 agents × 3 rounds × 1 probe/round.
After each cycle, compute issue discovery rate. Halt when rate converges
(N_i < 1 for 2 consecutive cycles) or at 50 topics.

## Tracking Table

### Cycle 1 — First-pass councils (original codebase)

| # | Topic | R1 Raised | R2 Survived | R3 Fixed | Cumulative Fixed |
|---|-------|-----------|-------------|----------|------------------|
| 1 | Bugs | 7 | 5 | 4 | 4 |
| 2 | Logs | 8 | 6 | 5 | 9 |
| 3 | Analytics | 5 | 2 | 2 | 11 |
| 4 | UX | 9 | 8 | 8 | 19 |
| 5 | Shell Integration | 7 | 3 | 3 | 22 |
| 6 | Config System | 5 | 2 | 2 | 24 |
| 7 | CLI Ergonomics | 5 | 3 | 3 | 27 |
| 8 | Data Lifecycle | 6 | 2 | 2 | 29 |
| 9 | Error Messages | 4 | 4 | 4 | 33 |
| 10 | Installation & Upgrade | 3 | 2 | 2 | 35 |
| | **Total** | **59** | **37** | **35** | **35** |

### Cycle 2 — Re-evaluation on patched codebase

| # | Topic | R1 Raised | R2 Survived | R3 Fixed | Cumulative Fixed |
|---|-------|-----------|-------------|----------|------------------|
| 1 | Bugs | 5 | 5 | 5 | 40 |
| 2 | Logs | 6 | 5 | 5 | 45 |
| 3 | Analytics | 3 | 2 | 2 | 47 |
| 4 | UX | 3 | 1 | 1 | 48 |
| 5 | Shell Integration | 2 | 1 | 1 | 49 |
| 6 | Config System | 1 | 0 | 0 | 49 |
| 7 | CLI Ergonomics | 2 | 2 | 2 | 51 |
| 8 | Data Lifecycle | 1 | 1 | 1 | 52 |
| 9 | Error Messages | 4 | 4 | 4 | 56 |
| 10 | Installation & Upgrade | 3 | 2 | 2 | 58 |
| | **Total** | **30** | **23** | **23** | **58** |

### Cycle 3 — Second re-evaluation (current patched codebase)

| # | Topic | R1 Raised | R2 Survived | R3 Fixed | Cumulative Fixed |
|---|-------|-----------|-------------|----------|------------------|
| 1 | Bugs | (see below) | | | |
| ... |  |  | | | |

---

## Convergence Model

### Issue discovery rate per cycle

```
Cycle 1: 35 issues fixed (59 raised, 37 survived)
Cycle 2: 23 issues fixed (30 raised, 23 survived)
```

### Decay calculation

Rate of decline from Cycle 1 → Cycle 2:
- Issues raised: 59 → 30 = **49% reduction** (λ = 0.68)
- Issues fixed: 35 → 23 = **34% reduction** (λ = 0.42)

Using exponential decay model N_i = N_0 × e^(-λ × (i-1)):

**Issues raised:**
- N_0 = 59, λ_raised = 0.68
- Predicted Cycle 3: 59 × e^(-0.68 × 2) = **15 issues raised**
- Convergence threshold (< 1 issue/cycle): Cycle 7 (59 × e^(-0.68 × 6) = 0.8)

**Issues fixed:**
- N_0 = 35, λ_fixed = 0.42
- Predicted Cycle 3: 35 × e^(-0.42 × 2) = **15 issues fixed**
- Convergence threshold (< 1 issue/cycle): Cycle 10 (35 × e^(-0.42 × 9) = 0.9)

### Alert: Configuration gap check

Cycle 2's Config System council returned 0 fixes. This is the first council
to hit zero — the config validation work from earlier councils covered all
remaining gaps. This is a leading indicator that the re-evaluation process
is converging on specific topics faster than others.

### Decision: Continue to Cycle 3

The current discovery rate (23 fixes/cycle) is well above the convergence
threshold (1 fix/cycle). The decay model predicts ~15 issues will be found
in Cycle 3, declining to ~1 by Cycle 10. With 50 topic budget available,
continuing is mathematically sound.

Lamda (λ) values will be recalculated after Cycle 3 to validate the model.
If the observed values are significantly higher than predicted (> 2σ),
the exponential model is wrong and a different distribution should be used.
