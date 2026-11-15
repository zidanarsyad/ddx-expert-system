"""Certainty-factor algebra (MYCIN / EMYCIN) and its probabilistic reading.

* Shortliffe & Buchanan (1975): a certainty factor (CF) in [-1, 1] measures the change of belief in a hypothesis
  caused by evidence; rules propagate CF = rule CF x premise CF, and independent rules that conclude the same
  hypothesis are combined with the parallel-combination function below.
* Heckerman (1986): if a CF is defined from the likelihood ratio lr = P(e | h) / P(e | not h) as
        CF = (lr - 1) / lr   for lr >= 1,        CF = lr - 1   for lr < 1,
  then the parallel combination of CFs is exactly the product of likelihood ratios, i.e. Bayesian updating
  of the odds under conditional independence. This is how the knowledge-base rules are calibrated from data.
"""
import math

THRESHOLD = 0.2          # EMYCIN: a premise with CF below 0.2 does not fire a rule
CF_LIMIT = 0.999999      # keep CFs strictly inside (-1, 1) so that conversions stay finite


def clamp(cf: float) -> float:
    return max(-CF_LIMIT, min(CF_LIMIT, cf))


def combine(a: float, b: float) -> float:
    """MYCIN parallel combination of two certainty factors for the same hypothesis."""
    if a >= 0 and b >= 0:
        return a + b * (1 - a)
    if a <= 0 and b <= 0:
        return a + b * (1 + a)
    return (a + b) / (1 - min(abs(a), abs(b)))


def combine_all(cfs) -> float:
    total = 0.0
    for cf in cfs:
        total = combine(total, clamp(cf))
    return total


def cf_from_lr(lr: float) -> float:
    """Heckerman's mapping of a likelihood ratio to a certainty factor."""
    if lr <= 0:
        return -CF_LIMIT
    return clamp((lr - 1) / lr if lr >= 1 else lr - 1)


def lr_from_cf(cf: float) -> float:
    """Inverse of cf_from_lr."""
    cf = clamp(cf)
    return 1 / (1 - cf) if cf >= 0 else 1 + cf


def posterior_odds(prior: float, cf: float) -> float:
    """Odds of the hypothesis after the evidence summarised by cf (Heckerman's interpretation)."""
    prior = min(max(prior, 1e-9), 1 - 1e-9)
    return prior / (1 - prior) * lr_from_cf(cf)


def log_odds(prior: float, cf: float) -> float:
    return math.log(posterior_odds(prior, cf))
