"""Pre-written helpers a specialist agent may import instead of writing them again.

    from toolbox import interpolate, lookup, require, finding

A copy of this file sits beside every generated function, so each one still runs on its own.
"""


def interpolate(x, rows, x_key, y_key):
    """Linear interpolation between measured rows. Raises ValueError outside the measured range."""
    rows = sorted(rows, key=lambda row: row[x_key])
    if not rows:
        raise ValueError("no measures were provided")
    low, high = rows[0][x_key], rows[-1][x_key]
    if not low <= x <= high:
        raise ValueError(f"{x_key} {x} is outside the measured range {low} to {high}")
    for a, b in zip(rows, rows[1:]):
        if a[x_key] <= x <= b[x_key]:
            span = b[x_key] - a[x_key]
            fraction = 0.0 if span == 0 else (x - a[x_key]) / span
            return a[y_key] + fraction * (b[y_key] - a[y_key])
    return rows[0][y_key]


def lookup(name, rows, key):
    """The measured row whose `key` equals `name`. An unknown name is an error, never a guess."""
    table = {row[key]: row for row in rows}
    if name not in table:
        raise ValueError(f"{key} '{name}' is not in the measures. Known: {sorted(table)}")
    return table[name]


def require(inputs, names):
    missing = [name for name in names if name not in inputs]
    if missing:
        raise ValueError(f"missing inputs: {missing}")


def finding(rule, domain, message, evidence):
    return {"rule": rule, "severity": "error", "domains": [domain], "message": message, "evidence": evidence}
