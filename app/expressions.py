"""Formatting helpers for rational coefficient expressions."""
from fractions import Fraction
from typing import Mapping


def format_fraction(value: Fraction) -> str:
    """Render a fraction as ``"p/q"`` (or ``"p"`` for whole numbers)."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def parse_fraction(text: str) -> Fraction:
    return Fraction(text)


def _coef_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(abs(value.numerator))
    return f"{abs(value.numerator)}/{value.denominator}"


def format_expression(coefficients: Mapping[str, Fraction]) -> str:
    """Render ``{district_code: coefficient}`` as an algebraic expression.

    Example: ``{"o1": 1/2, "o2": -3}`` -> ``"1/2*o1 - 3*o2"``.
    Zero coefficients are skipped; an empty mapping renders as ``"0"``.
    """
    terms = []
    for code, coef in coefficients.items():
        if coef == 0:
            continue
        sign = "-" if coef < 0 else "+"
        if abs(coef) == 1:
            terms.append(f"{sign} {code}")
        else:
            terms.append(f"{sign} {_coef_text(coef)}*{code}")
    if not terms:
        return "0"
    text = " ".join(terms)
    if text.startswith("+ "):
        text = text[2:]
    elif text.startswith("- "):
        text = "-" + text[2:]
    return text
