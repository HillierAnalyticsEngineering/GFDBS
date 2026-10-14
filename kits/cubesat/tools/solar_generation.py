def solar_generation(array_area_m2: float, cell_efficiency: float, pointing_factor: float, sunlit_fraction: float) -> dict:
    """Orbit-average solar generation in watts at beginning of life, for a quick sanity check."""
    sunlit_w = 1361.0 * array_area_m2 * cell_efficiency * pointing_factor
    return {"sunlit_w": round(sunlit_w, 2), "orbit_average_w": round(sunlit_w * sunlit_fraction, 2)}
