def thermal_balance(absorbed_w: float, emissivity: float, radiating_area_m2: float) -> dict:
    """Equilibrium temperature in degrees Celsius of one node that radiates the absorbed heat."""
    kelvin = (absorbed_w / (emissivity * 5.670374e-8 * radiating_area_m2)) ** 0.25
    return {"temperature_c": round(kelvin - 273.15, 1)}
