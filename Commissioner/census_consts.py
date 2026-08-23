def get_census_era(year: int) -> str:
    if year <= 1840:
        return "pre1850"
    if year <= 1870:
        return "heuristic"
    return "relationship"
