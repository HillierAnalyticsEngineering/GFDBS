import math


def link_budget(tx_power_w: float, tx_gain_dbi: float, rx_gain_dbi: float, frequency_mhz: float,
                slant_range_km: float, data_rate_kbps: float, noise_temp_k: float, loss_db: float = 3.0) -> dict:
    """Downlink Eb/N0 in dB from transmitter, antennas, range, and data rate, for a quick sanity check."""
    eirp = 10 * math.log10(tx_power_w) + tx_gain_dbi
    path_loss = 32.45 + 20 * math.log10(frequency_mhz) + 20 * math.log10(slant_range_km)
    cn0 = eirp - path_loss - loss_db + rx_gain_dbi - 10 * math.log10(noise_temp_k) + 228.6
    return {"eirp_dbw": round(eirp, 2), "path_loss_db": round(path_loss, 2),
            "ebn0_db": round(cn0 - 10 * math.log10(data_rate_kbps * 1000), 2)}
