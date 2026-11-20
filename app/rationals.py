"""Exact rational linear algebra built on :mod:`fractions`.

Everything here works with :class:`fractions.Fraction` so that coefficients
and populations are never approximated by floating point numbers.
"""
from fractions import Fraction
from functools import reduce
from math import gcd, lcm
from typing import List, Optional, Sequence, Tuple

Vec = List[Fraction]
Mat = List[List[Fraction]]


def rref(mat: Mat) -> Tuple[Mat, List[int]]:
    """Return (reduced row echelon form, pivot column indices)."""
    m = [row[:] for row in mat]
    if not m:
        return m, []
    rows, cols = len(m), len(m[0])
    pivots: List[int] = []
    r = 0
    for c in range(cols):
        pivot = next((i for i in range(r, rows) if m[i][c] != 0), None)
        if pivot is None:
            continue
        m[r], m[pivot] = m[pivot], m[r]
        pv = m[r][c]
        m[r] = [x / pv for x in m[r]]
        for i in range(rows):
            if i != r and m[i][c] != 0:
                factor = m[i][c]
                m[i] = [a - factor * b for a, b in zip(m[i], m[r])]
        pivots.append(c)
        r += 1
        if r == rows:
            break
    return m, pivots


def _unit(n: int, j: int) -> Vec:
    v = [Fraction(0) for _ in range(n)]
    v[j] = Fraction(1)
    return v


def nullspace(mat: Mat, n: Optional[int] = None) -> List[Vec]:
    """Basis of the null space of ``mat`` (vectors ``v`` with ``mat @ v = 0``).

    ``n`` is the number of columns; inferred from ``mat`` when possible.
    """
    if n is None:
        n = len(mat[0]) if mat else 0
    if not mat:
        return [_unit(n, j) for j in range(n)]
    rr, pivots = rref(mat)
    free_cols = [c for c in range(n) if c not in pivots]
    basis: List[Vec] = []
    for free in free_cols:
        v = [Fraction(0) for _ in range(n)]
        v[free] = Fraction(1)
        for i, col in enumerate(pivots):
            v[col] = -rr[i][free]
        basis.append(v)
    return basis


def solve(mat: Mat, rhs: Sequence[Fraction], n_vars: int) -> Optional[Vec]:
    """Solve ``mat @ x = rhs`` exactly.

    Returns one solution (free variables set to zero) or ``None`` when the
    system is inconsistent.
    """
    aug = [row[:] + [Fraction(rhs[i])] for i, row in enumerate(mat)]
    rr, pivots = rref(aug)
    for row in rr:
        if all(x == 0 for x in row[:n_vars]) and row[n_vars] != 0:
            return None
    x: Vec = [Fraction(0) for _ in range(n_vars)]
    for i, col in enumerate(pivots):
        if col < n_vars:  # pivots on the augmented column are zero-rhs rows
            x[col] = rr[i][n_vars]
    return x


def primitive_integer(v: Sequence[Fraction]) -> List[int]:
    """Scale a rational vector to a primitive integer vector.

    Denominators are cleared, the resulting integers divided by their gcd and
    the sign normalised so the first non-zero entry is positive.
    """
    if not v:
        return []
    den_lcm = reduce(lcm, (x.denominator for x in v), 1)
    ints = [int(x * den_lcm) for x in v]
    common = reduce(gcd, (abs(a) for a in ints), 0)
    if common:
        ints = [a // common for a in ints]
    first_nonzero = next((a for a in ints if a != 0), 0)
    if first_nonzero < 0:
        ints = [-a for a in ints]
    return ints
