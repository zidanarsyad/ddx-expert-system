"""Machine-learning baseline: a naive Bayes classifier over the same features and the same validation counts.

score(d) = log P(d) + sum over present features f of log P(f | d), Laplace-smoothed (the model of the course's
DDXPlus graph-database assignment). It is a black box compared with the rule engine -- it gives no reasons and
no triage -- and serves as the data-driven reference point in the evaluation and as the second opinion of the
hybrid mode (log-linear pooling of the two differentials).
"""
from __future__ import annotations

import math

from .kb import ALPHA


class NaiveBayes:
    def __init__(self, kb):
        self.kb = kb
        n_total = kb.n_total
        self.log_prior = {d: math.log(p.patients / n_total) for d, p in kb.pathologies.items()}
        self.log_default = {d: math.log(ALPHA / (p.patients + 2 * ALPHA)) for d, p in kb.pathologies.items()}
        self.loglik = {}
        for (d, f), c in kb._counts.items():
            self.loglik[(d, f)] = math.log((c + ALPHA) / (kb.pathologies[d].patients + 2 * ALPHA))

    def scores(self, case) -> dict:
        feats = [f for f, _ in case.features(self.kb)]
        return {d: lp + sum(self.loglik.get((d, f), self.log_default[d]) for f in feats) for d, lp in self.log_prior.items()}

    def posterior(self, case) -> dict:
        sc = self.scores(case)
        top = max(sc.values())
        w = {d: math.exp(s - top) for d, s in sc.items()}
        z = sum(w.values())
        return {d: x / z for d, x in w.items()}

    def rank(self, case) -> list:
        sc = self.scores(case)
        return sorted(sc, key=lambda d: -sc[d])


def pool(p_es: dict, p_nb: dict, weight: float = 0.5) -> dict:
    """Log-linear pooling of the expert-system and naive-Bayes distributions (hybrid mode)."""
    eps = 1e-12
    w = {d: math.exp((1 - weight) * math.log(p_es.get(d, 0) + eps) + weight * math.log(p_nb.get(d, 0) + eps)) for d in p_nb}
    z = sum(w.values())
    return {d: x / z for d, x in w.items()}
