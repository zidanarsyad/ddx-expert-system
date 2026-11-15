import itertools
import math

import pytest

from ddxes.certainty import THRESHOLD, cf_from_lr, combine, combine_all, lr_from_cf

CFS = [-0.9, -0.5, -0.2, 0.0, 0.3, 0.6, 0.95]


def test_combination_basic_cases():
    assert combine(0.6, 0.5) == pytest.approx(0.8)            # both positive: a + b(1 - a)
    assert combine(-0.6, -0.5) == pytest.approx(-0.8)         # both negative
    assert combine(0.6, -0.4) == pytest.approx(0.2 / 0.6)     # mixed: (a + b) / (1 - min(|a|, |b|))
    assert combine(0.7, 0.0) == pytest.approx(0.7)            # 0 is neutral
    assert THRESHOLD == 0.2


@pytest.mark.parametrize("a,b", list(itertools.product(CFS, CFS)))
def test_combination_is_commutative_and_bounded(a, b):
    assert combine(a, b) == pytest.approx(combine(b, a))
    assert -1 <= combine(a, b) <= 1


@pytest.mark.parametrize("a,b,c", [(0.3, 0.6, -0.2), (-0.5, 0.9, 0.4), (0.2, 0.2, 0.2)])
def test_combination_is_associative(a, b, c):
    assert combine(combine(a, b), c) == pytest.approx(combine(a, combine(b, c)))


@pytest.mark.parametrize("lr1,lr2", list(itertools.product([0.05, 0.4, 1.0, 2.5, 20.0], repeat=2)))
def test_heckerman_combination_is_product_of_likelihood_ratios(lr1, lr2):
    """The equivalence that lets the engine combine certainty factors as a sum of log likelihood ratios."""
    assert combine(cf_from_lr(lr1), cf_from_lr(lr2)) == pytest.approx(cf_from_lr(lr1 * lr2), abs=1e-9)


@pytest.mark.parametrize("cf", CFS)
def test_lr_cf_inverse(cf):
    assert cf_from_lr(lr_from_cf(cf)) == pytest.approx(cf)


def test_combine_all_matches_log_sum():
    cfs = [0.6, -0.3, 0.2, 0.45, -0.1]
    via_logs = cf_from_lr(math.exp(sum(math.log(lr_from_cf(c)) for c in cfs)))
    assert combine_all(cfs) == pytest.approx(via_logs)
