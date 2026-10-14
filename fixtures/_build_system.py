"""Writes the saved answers that `python build.py --offline` replays.

These stand in for what the builder and critic agents return in a live run.
They were written by hand so the offline replay is deterministic, and two of them are
deliberately flawed so the replay shows the checks working:

  builder.link.a1     uses the term required_margin in dB. The power function already
                      defined it as a ratio, so the shared dictionary refuses the commit.
  builder.thermal     leaves the unit out of one parameter description. No test catches
                      that. The critic agent does.

Run:  python fixtures/_build_system.py

The other studio answers (parameterize, interfaces, review, inputs, i2.*) are plain JSON files in this folder.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

INTERP = '''
def _interpolate(x, rows, x_key, y_key):
    """Linear interpolation between measured rows. No extrapolation."""
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


def _require(inputs, names):
    missing = [name for name in names if name not in inputs]
    if missing:
        raise ValueError(f"missing inputs: {missing}")


def _finding(rule, domain, message, evidence):
    return {"rule": rule, "severity": "error", "domains": [domain], "message": message, "evidence": evidence}
'''

POWER = '''"""End-of-life power margin for a small satellite. Standard library only."""

RULE_ID = "V-EOLPWR"
SOLAR_CONSTANT_W_M2 = 1361.0
DEFAULT_REQUIRED_MARGIN = 0.10

PARAMETERS = {
    "array_area_m2": "Total solar cell area, in m2",
    "cell_efficiency": "Beginning-of-life cell efficiency, a ratio from 0 to 1 with no unit",
    "pointing_factor": "Average cosine of the sun angle on the array, a ratio from 0 to 1 with no unit",
    "sunlit_fraction": "Fraction of the orbit in sunlight, a ratio from 0 to 1 with no unit",
    "load_avg_w": "Orbit-average electrical load, in W",
    "life_years": "Design life of the mission, in years",
    "required_margin": "Required power margin, a ratio with no unit. Optional, default 0.10",
}
''' + INTERP + '''

def evaluate(inputs, measures):
    """Orbit-average generation at end of life, and its margin over the load."""
    _require(inputs, ("array_area_m2", "cell_efficiency", "pointing_factor", "sunlit_fraction", "load_avg_w", "life_years"))
    for name in ("cell_efficiency", "pointing_factor", "sunlit_fraction"):
        if not 0 <= inputs[name] <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
    if inputs["load_avg_w"] <= 0 or inputs["array_area_m2"] <= 0:
        raise ValueError("load_avg_w and array_area_m2 must be positive")
    required = inputs.get("required_margin", DEFAULT_REQUIRED_MARGIN)
    relative = _interpolate(inputs["life_years"], measures, "life_years", "relative_output")
    sunlit = SOLAR_CONSTANT_W_M2 * inputs["array_area_m2"] * inputs["cell_efficiency"] * inputs["pointing_factor"]
    generation = sunlit * relative * inputs["sunlit_fraction"]
    margin = generation / inputs["load_avg_w"] - 1
    return {
        "relative_output": round(relative, 4),
        "generation_avg_w": round(generation, 2),
        "margin": round(margin, 2),
        "required_margin": required,
        "passes": margin >= required,
    }


def check(inputs, measures):
    """Return a finding when end-of-life generation does not cover the load with margin."""
    result = evaluate(inputs, measures)
    if result["passes"]:
        return []
    message = (f"End-of-life power margin is {result['margin']:.0%}. The requirement is {result['required_margin']:.0%}. "
               f"Generation {result['generation_avg_w']} W against a load of {inputs['load_avg_w']} W "
               f"after {inputs['life_years']} years.")
    return [_finding(RULE_ID, "power", message, {**result, **dict(inputs)})]
'''
POWER_TESTS = '''import pytest
from solution import PARAMETERS, check, evaluate

AGING = [{"life_years": 0, "relative_output": 1.0}, {"life_years": 10, "relative_output": 0.8}]
BASE = {"array_area_m2": 0.1, "cell_efficiency": 0.3, "pointing_factor": 0.5, "sunlit_fraction": 0.6,
        "load_avg_w": 5.0, "life_years": 0}


def test_generation_at_beginning_of_life():
    assert evaluate(BASE, AGING)["generation_avg_w"] == pytest.approx(1361 * 0.1 * 0.3 * 0.5 * 0.6, abs=0.01)


def test_aging_is_interpolated():
    assert evaluate({**BASE, "life_years": 5}, AGING)["relative_output"] == pytest.approx(0.9)


def test_no_extrapolation_past_the_data():
    with pytest.raises(ValueError):
        evaluate({**BASE, "life_years": 11}, AGING)


def test_margin_and_finding():
    hungry = {**BASE, "load_avg_w": 20.0}
    assert evaluate(hungry, AGING)["passes"] is False
    finding = check(hungry, AGING)[0]
    assert finding["rule"] == "V-EOLPWR" and "generation_avg_w" in finding["evidence"]


def test_program_can_set_the_required_margin():
    assert evaluate({**BASE, "required_margin": 5.0}, AGING)["passes"] is False


def test_ratios_must_be_ratios():
    with pytest.raises(ValueError):
        evaluate({**BASE, "cell_efficiency": 29}, AGING)


def test_every_parameter_states_a_unit():
    assert all((" in " in d) or ("no unit" in d) for d in PARAMETERS.values())
'''

RADIATION = '''"""Total ionizing dose assessment for the electronic parts of a small satellite. Standard library only."""

RULE_ID = "V-RAD"
DEFAULT_REQUIRED_RDM = 2.0

PARAMETERS = {
    "shield_mm": "Aluminum-equivalent shielding around the part, in mm",
    "life_years": "Design life of the mission, in years",
    "tolerance_krad": "Rated total-dose tolerance of the part, in krad",
    "required_rdm": "Required radiation design margin, a ratio with no unit. Optional, default 2.0",
}
''' + INTERP + '''

def evaluate(inputs, measures):
    """Mission dose and radiation design margin for one part."""
    _require(inputs, ("shield_mm", "life_years", "tolerance_krad"))
    if inputs["life_years"] <= 0 or inputs["tolerance_krad"] <= 0:
        raise ValueError("life_years and tolerance_krad must be positive")
    required = inputs.get("required_rdm", DEFAULT_REQUIRED_RDM)
    annual = _interpolate(inputs["shield_mm"], measures, "shield_mm", "krad_per_year")
    mission = annual * inputs["life_years"]
    rdm = inputs["tolerance_krad"] / mission
    return {
        "annual_dose_krad": round(annual, 3),
        "mission_dose_krad": round(mission, 3),
        "rdm": round(rdm, 2),
        "required_rdm": required,
        "passes": rdm >= required,
    }


def check(inputs, measures):
    """Return a finding when the part does not meet the required margin."""
    result = evaluate(inputs, measures)
    if result["passes"]:
        return []
    message = (f"Radiation design margin is {result['rdm']}. The requirement is {result['required_rdm']}. "
               f"Mission dose {result['mission_dose_krad']} krad behind {inputs['shield_mm']} mm, "
               f"part rated {inputs['tolerance_krad']} krad.")
    return [_finding(RULE_ID, "radiation", message, {**result, **dict(inputs)})]
'''
RADIATION_TESTS = '''import pytest
from solution import PARAMETERS, check, evaluate

TABLE = [{"shield_mm": 1.0, "krad_per_year": 10.0}, {"shield_mm": 3.0, "krad_per_year": 2.0}]


def test_dose_interpolates_between_points():
    assert evaluate({"shield_mm": 2.0, "life_years": 1, "tolerance_krad": 50}, TABLE)["annual_dose_krad"] == pytest.approx(6.0)


def test_row_order_does_not_matter():
    assert evaluate({"shield_mm": 2.0, "life_years": 1, "tolerance_krad": 50}, list(reversed(TABLE)))["annual_dose_krad"] == pytest.approx(6.0)


def test_no_extrapolation():
    for shield in (0.5, 3.5):
        with pytest.raises(ValueError):
            evaluate({"shield_mm": shield, "life_years": 1, "tolerance_krad": 50}, TABLE)


def test_empty_measures_rejected():
    with pytest.raises(ValueError):
        evaluate({"shield_mm": 1.0, "life_years": 1, "tolerance_krad": 10}, [])


def test_margin_and_pass():
    r = evaluate({"shield_mm": 3.0, "life_years": 2, "tolerance_krad": 10}, TABLE)
    assert r["mission_dose_krad"] == 4.0 and r["rdm"] == 2.5 and r["passes"] is True


def test_program_can_raise_the_required_margin():
    assert evaluate({"shield_mm": 3.0, "life_years": 2, "tolerance_krad": 10, "required_rdm": 3.0}, TABLE)["passes"] is False


def test_finding_carries_evidence():
    f = check({"shield_mm": 1.0, "life_years": 2, "tolerance_krad": 10}, TABLE)
    assert len(f) == 1 and f[0]["rule"] == "V-RAD" and f[0]["evidence"]["mission_dose_krad"] == 20.0


def test_every_parameter_states_a_unit():
    assert all((" in " in d) or ("no unit" in d) for d in PARAMETERS.values())
'''

MASS = '''"""Mass against the launch limit, with a growth allowance set by design maturity. Standard library only."""

RULE_ID = "V-MASSLIM"

PARAMETERS = {
    "dry_mass_g": "Current best estimate of dry mass, in g",
    "propellant_mass_g": "Loaded propellant mass, in g",
    "maturity": "Design maturity level, one of the levels named in the measures. A name with no unit",
    "launch_limit_g": "Maximum mass the launch provider accepts, in g",
}


def _require(inputs, names):
    missing = [name for name in names if name not in inputs]
    if missing:
        raise ValueError(f"missing inputs: {missing}")


def _allowance(maturity, measures):
    """Growth allowance for a maturity level. An unknown level is an error, never a guess."""
    table = {row["maturity"]: row["growth_allowance"] for row in measures}
    if maturity not in table:
        raise ValueError(f"maturity '{maturity}' is not in the measures. Known levels: {sorted(table)}")
    return table[maturity]


def evaluate(inputs, measures):
    """Mass with growth allowance, and the headroom under the launch limit."""
    _require(inputs, ("dry_mass_g", "propellant_mass_g", "maturity", "launch_limit_g"))
    if inputs["dry_mass_g"] <= 0 or inputs["propellant_mass_g"] < 0 or inputs["launch_limit_g"] <= 0:
        raise ValueError("masses must be positive, and propellant must not be negative")
    allowance = _allowance(inputs["maturity"], measures)
    total = inputs["dry_mass_g"] * (1 + allowance) + inputs["propellant_mass_g"]
    headroom = inputs["launch_limit_g"] - total
    return {
        "growth_allowance": allowance,
        "mass_with_growth_g": round(total, 1),
        "headroom_g": round(headroom, 1),
        "passes": headroom >= 0,
    }


def check(inputs, measures):
    """Return a finding when the mass with growth exceeds the launch limit."""
    result = evaluate(inputs, measures)
    if result["passes"]:
        return []
    message = (f"Mass with {result['growth_allowance']:.0%} growth allowance is {result['mass_with_growth_g']} g. "
               f"The launch limit is {inputs['launch_limit_g']} g. Over by {-result['headroom_g']} g.")
    return [{"rule": RULE_ID, "severity": "error", "domains": ["mass"], "message": message,
             "evidence": {**result, **dict(inputs)}}]
'''
MASS_TESTS = '''import pytest
from solution import PARAMETERS, check, evaluate

LEVELS = [{"maturity": "estimated", "growth_allowance": 0.2}, {"maturity": "measured", "growth_allowance": 0.0}]
BASE = {"dry_mass_g": 1000, "propellant_mass_g": 100, "maturity": "estimated", "launch_limit_g": 1500}


def test_allowance_applies_to_dry_mass_only():
    assert evaluate(BASE, LEVELS)["mass_with_growth_g"] == 1300.0


def test_measured_hardware_gets_no_allowance():
    assert evaluate({**BASE, "maturity": "measured"}, LEVELS)["mass_with_growth_g"] == 1100.0


def test_unknown_maturity_is_an_error():
    with pytest.raises(ValueError):
        evaluate({**BASE, "maturity": "guessed"}, LEVELS)


def test_over_the_limit_gives_a_finding():
    over = {**BASE, "launch_limit_g": 1200}
    assert evaluate(over, LEVELS)["headroom_g"] == -100.0
    assert check(over, LEVELS)[0]["rule"] == "V-MASSLIM"


def test_exactly_at_the_limit_passes():
    assert evaluate({**BASE, "launch_limit_g": 1300}, LEVELS)["passes"] is True


def test_negative_propellant_rejected():
    with pytest.raises(ValueError):
        evaluate({**BASE, "propellant_mass_g": -1}, LEVELS)
'''

LINK = '''"""Downlink margin for a small satellite. Standard library only."""
import math

RULE_ID = "V-LINKM"
BOLTZMANN_DBW_HZ_K = -228.6
DEFAULT_REQUIRED_MARGIN_DB = 3.0

PARAMETERS = {
    "tx_power_w": "Transmitter output power, in W",
    "tx_gain_dbi": "Transmit antenna gain, in dBi",
    "rx_gain_dbi": "Ground antenna gain, in dBi",
    "frequency_mhz": "Carrier frequency, in MHz",
    "slant_range_km": "Distance from spacecraft to ground station, in km",
    "elevation_deg": "Elevation angle at the ground station, in deg",
    "data_rate_kbps": "Information data rate, in kbps",
    "noise_temp_k": "Receive system noise temperature, in K",
    "required_ebn0_db": "Eb/N0 the modulation and coding need, in dB",
    "__REQ__": "Required link margin, in dB. Optional, default 3.0",
}
''' + INTERP + '''

def evaluate(inputs, measures):
    """Eb/N0 at the ground station and its margin over what the modulation needs."""
    _require(inputs, ("tx_power_w", "tx_gain_dbi", "rx_gain_dbi", "frequency_mhz", "slant_range_km",
                      "elevation_deg", "data_rate_kbps", "noise_temp_k", "required_ebn0_db"))
    for name in ("tx_power_w", "frequency_mhz", "slant_range_km", "data_rate_kbps", "noise_temp_k"):
        if inputs[name] <= 0:
            raise ValueError(f"{name} must be positive")
    required = inputs.get("__REQ__", DEFAULT_REQUIRED_MARGIN_DB)
    loss = _interpolate(inputs["elevation_deg"], measures, "elevation_deg", "loss_db")
    eirp = 10 * math.log10(inputs["tx_power_w"]) + inputs["tx_gain_dbi"]
    path_loss = 32.45 + 20 * math.log10(inputs["frequency_mhz"]) + 20 * math.log10(inputs["slant_range_km"])
    cn0 = (eirp - path_loss - loss + inputs["rx_gain_dbi"]
           - 10 * math.log10(inputs["noise_temp_k"]) - BOLTZMANN_DBW_HZ_K)
    ebn0 = cn0 - 10 * math.log10(inputs["data_rate_kbps"] * 1000)
    margin = ebn0 - inputs["required_ebn0_db"]
    return {
        "loss_db": round(loss, 2),
        "path_loss_db": round(path_loss, 2),
        "ebn0_db": round(ebn0, 2),
        "margin_db": round(margin, 2),
        "__REQ__": required,
        "passes": margin >= required,
    }


def check(inputs, measures):
    """Return a finding when the link does not close with the required margin."""
    result = evaluate(inputs, measures)
    if result["passes"]:
        return []
    message = (f"Link margin is {result['margin_db']} dB. The requirement is {result['__REQ__']} dB. "
               f"Eb/N0 {result['ebn0_db']} dB at {inputs['elevation_deg']} deg elevation and "
               f"{inputs['data_rate_kbps']} kbps.")
    return [_finding(RULE_ID, "link", message, {**result, **dict(inputs)})]
'''
LINK_TESTS = '''import math
import pytest
from solution import PARAMETERS, check, evaluate

LOSSES = [{"elevation_deg": 10, "loss_db": 3.0}, {"elevation_deg": 90, "loss_db": 1.0}]
BASE = {"tx_power_w": 1.0, "tx_gain_dbi": 0.0, "rx_gain_dbi": 30.0, "frequency_mhz": 1000.0, "slant_range_km": 1000.0,
        "elevation_deg": 10, "data_rate_kbps": 100.0, "noise_temp_k": 100.0, "required_ebn0_db": 10.0}


def test_path_loss_matches_the_textbook_form():
    assert evaluate(BASE, LOSSES)["path_loss_db"] == pytest.approx(32.45 + 60 + 60, abs=0.01)


def test_ebn0_from_first_principles():
    expected = 0 - 152.45 - 3.0 + 30 - 20 + 228.6 - 50
    assert evaluate(BASE, LOSSES)["ebn0_db"] == pytest.approx(expected, abs=0.01)


def test_doubling_power_adds_three_db():
    a, b = evaluate(BASE, LOSSES), evaluate({**BASE, "tx_power_w": 2.0}, LOSSES)
    assert b["margin_db"] - a["margin_db"] == pytest.approx(10 * math.log10(2), abs=0.02)


def test_loss_is_interpolated_by_elevation():
    assert evaluate({**BASE, "elevation_deg": 50}, LOSSES)["loss_db"] == pytest.approx(2.0)


def test_no_extrapolation_below_the_lowest_elevation():
    with pytest.raises(ValueError):
        evaluate({**BASE, "elevation_deg": 5}, LOSSES)


def test_finding_when_the_link_does_not_close():
    weak = {**BASE, "data_rate_kbps": 100000.0}
    assert evaluate(weak, LOSSES)["passes"] is False
    assert check(weak, LOSSES)[0]["rule"] == "V-LINKM"


def test_program_can_set_the_required_margin():
    assert evaluate({**BASE, "__REQ__": 99.0}, LOSSES)["passes"] is False
'''

THERMAL = '''"""Single-node equilibrium temperature for a small satellite. Standard library only."""

RULE_ID = "V-TEMP"
STEFAN_BOLTZMANN = 5.670374e-8

PARAMETERS = {
    "case": "Environment case, one of the cases named in the measures. A name with no unit",
    "absorptivity": "Solar absorptivity of the outer surface, a ratio from 0 to 1 with no unit",
    "emissivity": "Infrared emissivity of the outer surface, a ratio from 0 to 1 with no unit",
    "projected_area_m2": "Area facing the sun and the Earth, in m2",
    "radiating_area_m2": "Total area that radiates to space, in m2",
    "internal_power_w": "__INTERNAL__",
    "temp_min_c": "Lowest allowed bulk temperature, in degC",
    "temp_max_c": "Highest allowed bulk temperature, in degC",
}


def _require(inputs, names):
    missing = [name for name in names if name not in inputs]
    if missing:
        raise ValueError(f"missing inputs: {missing}")


def _environment(case, measures):
    """Fluxes for a named case. An unknown case is an error, never a guess."""
    table = {row["case"]: row for row in measures}
    if case not in table:
        raise ValueError(f"case '{case}' is not in the measures. Known cases: {sorted(table)}")
    return table[case]


def evaluate(inputs, measures):
    """Bulk temperature where absorbed and internal heat equal radiated heat."""
    _require(inputs, ("case", "absorptivity", "emissivity", "projected_area_m2", "radiating_area_m2",
                      "internal_power_w", "temp_min_c", "temp_max_c"))
    for name in ("absorptivity", "emissivity"):
        if not 0 < inputs[name] <= 1:
            raise ValueError(f"{name} must be above 0 and at most 1")
    if inputs["projected_area_m2"] <= 0 or inputs["radiating_area_m2"] <= 0 or inputs["internal_power_w"] < 0:
        raise ValueError("areas must be positive, and internal_power_w must not be negative")
    env = _environment(inputs["case"], measures)
    absorbed = (inputs["absorptivity"] * env["solar_w_m2"] * inputs["projected_area_m2"]
                + inputs["emissivity"] * env["earth_ir_w_m2"] * inputs["projected_area_m2"]
                + inputs["internal_power_w"])
    kelvin = (absorbed / (inputs["emissivity"] * STEFAN_BOLTZMANN * inputs["radiating_area_m2"])) ** 0.25
    celsius = kelvin - 273.15
    return {
        "absorbed_w": round(absorbed, 2),
        "temperature_c": round(celsius, 1),
        "passes": inputs["temp_min_c"] <= celsius <= inputs["temp_max_c"],
    }


def check(inputs, measures):
    """Return a finding when the bulk temperature leaves the allowed range."""
    result = evaluate(inputs, measures)
    if result["passes"]:
        return []
    message = (f"Bulk temperature in the {inputs['case']} case is {result['temperature_c']} degC. "
               f"The allowed range is {inputs['temp_min_c']} to {inputs['temp_max_c']} degC.")
    return [{"rule": RULE_ID, "severity": "error", "domains": ["thermal"], "message": message,
             "evidence": {**result, **dict(inputs)}}]
'''
THERMAL_TESTS = '''import pytest
from solution import PARAMETERS, check, evaluate

CASES = [{"case": "sun", "solar_w_m2": 1000.0, "earth_ir_w_m2": 0.0},
         {"case": "dark", "solar_w_m2": 0.0, "earth_ir_w_m2": 0.0}]
BASE = {"case": "sun", "absorptivity": 1.0, "emissivity": 1.0, "projected_area_m2": 1.0, "radiating_area_m2": 4.0,
        "internal_power_w": 0.0, "temp_min_c": -40, "temp_max_c": 60}


def test_black_sphere_balance():
    expected_k = (1000.0 / (5.670374e-8 * 4.0)) ** 0.25
    assert evaluate(BASE, CASES)["temperature_c"] == pytest.approx(expected_k - 273.15, abs=0.1)


def test_internal_power_warms_it():
    assert evaluate({**BASE, "internal_power_w": 100.0}, CASES)["temperature_c"] > evaluate(BASE, CASES)["temperature_c"]


def test_more_radiating_area_cools_it():
    assert evaluate({**BASE, "radiating_area_m2": 8.0}, CASES)["temperature_c"] < evaluate(BASE, CASES)["temperature_c"]


def test_unknown_case_is_an_error():
    with pytest.raises(ValueError):
        evaluate({**BASE, "case": "safe mode"}, CASES)


def test_too_cold_gives_a_finding():
    cold = {**BASE, "case": "dark", "internal_power_w": 1.0}
    assert evaluate(cold, CASES)["passes"] is False
    assert check(cold, CASES)[0]["rule"] == "V-TEMP"


def test_zero_emissivity_rejected():
    with pytest.raises(ValueError):
        evaluate({**BASE, "emissivity": 0.0}, CASES)
'''

T = lambda name, unit, description: {"name": name, "unit": unit, "description": description}
PASSES = T("passes", "boolean", "True when the design meets the requirement")
LIFE = T("life_years", "years", "Design life of the mission")

TERMS = {
    "power": [T("array_area_m2", "m2", "Total solar cell area"), T("cell_efficiency", "ratio", "Beginning-of-life cell efficiency"),
              T("pointing_factor", "ratio", "Average cosine of the sun angle on the array"),
              T("sunlit_fraction", "ratio", "Fraction of the orbit in sunlight"), T("load_avg_w", "W", "Orbit-average electrical load"),
              LIFE, T("required_margin", "ratio", "Required power margin"),
              T("generation_avg_w", "W", "Orbit-average generation at end of life"), T("margin", "ratio", "Generation over load, minus one"),
              T("relative_output", "ratio", "Array output at end of life relative to beginning of life"), PASSES],
    "radiation": [T("shield_mm", "mm", "Aluminum-equivalent shielding around the part"), LIFE,
                  T("tolerance_krad", "krad", "Rated total-dose tolerance of the part"), T("required_rdm", "ratio", "Required radiation design margin"),
                  T("annual_dose_krad", "krad", "Dose per year behind the shielding"), T("mission_dose_krad", "krad", "Dose over the design life"),
                  T("rdm", "ratio", "Radiation design margin: tolerance over mission dose"), PASSES],
    "mass": [T("dry_mass_g", "g", "Current best estimate of dry mass"), T("propellant_mass_g", "g", "Loaded propellant mass"),
             T("maturity", "name", "Design maturity level"), T("launch_limit_g", "g", "Maximum mass the launch provider accepts"),
             T("growth_allowance", "ratio", "Mass growth allowance for the maturity level"),
             T("mass_with_growth_g", "g", "Dry mass with allowance, plus propellant"), T("headroom_g", "g", "Launch limit minus mass with growth"), PASSES],
    "thermal": [T("case", "name", "Environment case"), T("absorptivity", "ratio", "Solar absorptivity of the outer surface"),
                T("emissivity", "ratio", "Infrared emissivity of the outer surface"), T("projected_area_m2", "m2", "Area facing the sun and the Earth"),
                T("radiating_area_m2", "m2", "Total area that radiates to space"), T("internal_power_w", "W", "Heat dissipated inside the spacecraft"),
                T("temp_min_c", "degC", "Lowest allowed bulk temperature"), T("temp_max_c", "degC", "Highest allowed bulk temperature"),
                T("absorbed_w", "W", "Total heat absorbed and dissipated"), T("temperature_c", "degC", "Equilibrium bulk temperature"), PASSES],
}


def link_terms(required_name: str) -> list[dict]:
    return [T("tx_power_w", "W", "Transmitter output power"), T("tx_gain_dbi", "dBi", "Transmit antenna gain"),
            T("rx_gain_dbi", "dBi", "Ground antenna gain"), T("frequency_mhz", "MHz", "Carrier frequency"),
            T("slant_range_km", "km", "Distance from spacecraft to ground station"), T("elevation_deg", "deg", "Elevation angle at the ground station"),
            T("data_rate_kbps", "kbps", "Information data rate"), T("noise_temp_k", "K", "Receive system noise temperature"),
            T("required_ebn0_db", "dB", "Eb/N0 the modulation and coding need"), T(required_name, "dB", "Required link margin"),
            T("loss_db", "dB", "Atmospheric and implementation loss at this elevation"), T("path_loss_db", "dB", "Free-space path loss"),
            T("ebn0_db", "dB", "Eb/N0 at the ground station"), T("margin_db", "dB", "Eb/N0 minus required Eb/N0"), PASSES]


NOTES = {
    "power": "Method: sunlit generation from area, efficiency, and pointing. Scale by the array's relative output at the design life, then by the sunlit fraction. Margin is generation over load, minus one.\nValid range: design life inside the degradation measures. Ratios must lie between 0 and 1.\nWhat varies per spacecraft: array area, cell choice, pointing, orbit sunlight, load, and life.\nAssumption: one pointing factor stands for the whole orbit. A tumbling or sun-tracking spacecraft needs its own value.",
    "radiation": "Method: annual dose from the dose-depth measures by linear interpolation, times design life. Margin is rated tolerance over mission dose.\nValid range: shielding inside the measured thicknesses only. The curve is steep, so extrapolation is refused.\nWhat varies per spacecraft: shielding, life, part tolerance, and the required margin. The orbit enters only through the measures.\nAssumption: linear interpolation overstates dose between widely spaced points, which is the safe direction.",
    "mass": "Method: apply a growth allowance to dry mass, chosen by design maturity. Add propellant. Compare with the launch limit.\nValid range: maturity levels named in the measures. An unknown level is an error.\nWhat varies per spacecraft: dry mass, propellant, maturity, and the launch limit.\nAssumption: propellant mass is known and takes no allowance.",
    "link": "Method: EIRP, minus free-space path loss, minus a loss looked up by elevation, plus ground gain over noise temperature, gives carrier to noise density. Subtract the data rate to get Eb/N0. Margin is Eb/N0 minus what the modulation needs.\nValid range: elevation inside the loss measures.\nWhat varies per spacecraft: transmitter, antennas, frequency, range, data rate, and ground station.\nAssumption: slant range is an input. Orbit geometry belongs to another function.",
    "thermal": "Method: absorbed solar and Earth infrared heat plus internal dissipation equals radiated heat. Solve for one bulk temperature.\nValid range: environment cases named in the measures. A single node hides gradients, so treat the result as a screening value.\nWhat varies per spacecraft: surface finish, areas, internal power, and temperature limits.\nAssumption: the spacecraft is isothermal.",
}


def submission(name, module, tests, terms):
    return {"design_notes": NOTES[name], "terms": terms, "module_code": module, "test_code": tests}


def write(key, data):
    (HERE / f"{key}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


UNIT = "Heat dissipated inside the spacecraft, in W"
write("builder.power", submission("power", POWER, POWER_TESTS, TERMS["power"]))
write("builder.radiation", submission("radiation", RADIATION, RADIATION_TESTS, TERMS["radiation"]))
write("builder.mass", submission("mass", MASS, MASS_TESTS, TERMS["mass"]))
write("builder.link.a1", submission("link", LINK.replace("__REQ__", "required_margin"), LINK_TESTS.replace("__REQ__", "required_margin"), link_terms("required_margin")))
write("builder.link.a2", submission("link", LINK.replace("__REQ__", "required_margin_db"), LINK_TESTS.replace("__REQ__", "required_margin_db"), link_terms("required_margin_db")))
write("builder.thermal", submission("thermal", THERMAL.replace("__INTERNAL__", "Heat dissipated inside the spacecraft"), THERMAL_TESTS, TERMS["thermal"]))
write("builder.thermal.v2", submission("thermal", THERMAL.replace("__INTERNAL__", UNIT),
                                       THERMAL_TESTS + '\n\ndef test_every_parameter_states_a_unit():\n    assert all((" in " in d) or ("no unit" in d) for d in PARAMETERS.values())\n', TERMS["thermal"]))

# ---- drill-down: PowerBus decomposed into battery, conditioning, and harness. The power function gains two inputs ----
POWER_V2 = POWER.replace('''    "required_margin": "Required power margin, a ratio with no unit. Optional, default 0.10",
}''', '''    "required_margin": "Required power margin, a ratio with no unit. Optional, default 0.10",
    "conversion_efficiency": "Efficiency of the power conditioning unit between array and load, a ratio from 0 to 1 with no unit. Optional, default 1.0",
    "harness_loss_fraction": "Fraction of conditioned power lost in the harness, a ratio from 0 to 1 with no unit. Optional, default 0.0",
}''').replace('''    generation = sunlit * relative * inputs["sunlit_fraction"]
    margin = generation / inputs["load_avg_w"] - 1
    return {
        "relative_output": round(relative, 4),
        "generation_avg_w": round(generation, 2),''', '''    generation = sunlit * relative * inputs["sunlit_fraction"]
    conversion = inputs.get("conversion_efficiency", 1.0)
    harness_loss = inputs.get("harness_loss_fraction", 0.0)
    for name, value in (("conversion_efficiency", conversion), ("harness_loss_fraction", harness_loss)):
        if not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
    delivered = generation * conversion * (1 - harness_loss)
    margin = delivered / inputs["load_avg_w"] - 1
    return {
        "relative_output": round(relative, 4),
        "generation_avg_w": round(generation, 2),
        "delivered_avg_w": round(delivered, 2),''').replace(
    '"""Orbit-average generation at end of life, and its margin over the load."""',
    '"""Orbit-average generation at end of life, what the bus delivers after conditioning and harness losses, and the margin over the load."""')
assert POWER_V2 != POWER
POWER_V2_TESTS = POWER_TESTS + '''

def test_bus_losses_reduce_what_reaches_the_load():
    base = evaluate(BASE, AGING)
    lossy = evaluate({**BASE, "conversion_efficiency": 0.9, "harness_loss_fraction": 0.05}, AGING)
    assert lossy["generation_avg_w"] == base["generation_avg_w"]
    assert lossy["delivered_avg_w"] == pytest.approx(base["generation_avg_w"] * 0.9 * 0.95, abs=0.01)
    assert lossy["margin"] < base["margin"]


def test_bus_losses_default_to_none():
    assert evaluate(BASE, AGING)["delivered_avg_w"] == evaluate(BASE, AGING)["generation_avg_w"]
'''
NOTES["power.v2"] = NOTES["power"] + ("\nRevision: the PowerBus decomposition added a conditioning unit and a harness. Delivered power is generation times "
                                        "conversion efficiency times one minus the harness loss fraction. Margin uses delivered power. Both default to lossless, "
                                        "so the worked cases, which predate the bus breakdown, still hold.")
write("builder.power.v2", submission("power.v2", POWER_V2, POWER_V2_TESTS, TERMS["power"] + [
    T("conversion_efficiency", "ratio", "Efficiency of the power conditioning unit between array and load"),
    T("harness_loss_fraction", "ratio", "Fraction of conditioned power lost in the harness"),
    T("delivered_avg_w", "W", "Orbit-average power delivered to the load after bus losses")]))
write("studio.decompose", {
    "summary": "A power bus is a battery pack that rides through eclipse, a power conditioning unit that regulates array output to the load rails, and the harness that carries it. The conditioning efficiency and the harness loss stand between array generation and the load, so the margin should be taken on delivered power.",
    "parts": [
        {"name": "BatteryPack", "parent": "PowerBus", "specializes": "", "multiplicity": "1..*", "capability": "power", "description": "Secondary cells that carry the load through eclipse"},
        {"name": "PowerConditioningUnit", "parent": "PowerBus", "specializes": "", "multiplicity": "1", "capability": "power", "description": "Regulates array output onto the load rails"},
        {"name": "Harness", "parent": "PowerBus", "specializes": "", "multiplicity": "1", "capability": "power", "description": "Cabling and connectors between conditioning unit and loads"}],
    "attributes": [
        {"name": "conversion_efficiency", "unit": "ratio", "description": "Efficiency of the power conditioning unit between array and load", "part": "PowerConditioningUnit", "example": 0.95, "rationale": "Typical for a small-satellite MPPT and regulator chain"},
        {"name": "harness_loss_fraction", "unit": "ratio", "description": "Fraction of conditioned power lost in the harness", "part": "Harness", "example": 0.02, "rationale": "Two percent is usual for a short CubeSat harness"}],
    "method_change": "Delivered power is generation times conversion_efficiency times one minus harness_loss_fraction. Take the margin on delivered power, not on generation. Both inputs default to lossless so earlier worked cases still hold.",
    "notes": ["Battery capacity is not an attribute here: this function assesses orbit-average margin, not eclipse energy balance."]})
write("studio.decompose.review.1", {
    "verdict": "accept",
    "assessment": "The three parts are what a power engineer would name inside a bus, at one level, with sensible multiplicities. The two attributes sit on the parts that cause them and change the assessment: margin on delivered power is the right quantity. Leaving battery capacity out is correct for an orbit-average margin function.",
    "instruction": "",
    "lessons": ["When a bus or distribution part is decomposed, take the margin on delivered power rather than on generation, and default the loss terms to lossless so earlier worked cases still hold."]})

print("fixtures written")
