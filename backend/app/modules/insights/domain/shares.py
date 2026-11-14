"""Whole-number shares of a total, for the product split on Portfolio. Pure: no I/O.

The app draws the split as a bar with a label per product, so the shares must be whole
percentages that add up to exactly 100. Each is rounded down, and the points left over go to
the parts that lost the most in rounding (the largest remainder method). A tie goes to the
earlier part, so the same values always give the same shares.
"""


def whole_percentages(values: list[int]) -> list[int]:
    """Each value's share of their sum, as whole percentages adding up to 100.

    Every value is a whole number of the smallest currency unit and none is negative. An empty
    list, or one that adds up to zero, has no shares: every part is 0.
    """
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("values are whole, non-negative amounts")
    total = sum(values)
    if total == 0:
        return [0] * len(values)
    floors = [value * 100 // total for value in values]
    remainders = [value * 100 % total for value in values]
    left = 100 - sum(floors)
    # Most lost in rounding first; the earlier part first on a tie.
    order = sorted(range(len(values)), key=lambda index: (-remainders[index], index))
    for index in order[:left]:
        floors[index] += 1
    return floors
