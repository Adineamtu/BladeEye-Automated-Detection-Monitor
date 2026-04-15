from backend import signatures_data
from backend.signatures_data import match_rf_signature, capture_to_signature


def test_match_rf_signature_within_10_percent_tolerance() -> None:
    # Nexa: short=270, long=1300 -> +10% on both should still match
    match = match_rf_signature(short_pulse=297, long_pulse=1430)
    assert match is not None
    assert match["name"] == "Nexa"


def test_match_rf_signature_outside_tolerance_returns_none() -> None:
    # No known profile near these pulse timings.
    assert match_rf_signature(short_pulse=9999, long_pulse=8888) is None


def test_match_rf_signature_requires_both_pulses() -> None:
    assert match_rf_signature(short_pulse=270, long_pulse=None) is None


def test_match_rf_signature_supports_zero_long_pulse() -> None:
    # Oregon Scientific uses long_pulse=0; matching should still work.
    match = match_rf_signature(short_pulse=440, long_pulse=0)
    assert match is not None
    assert match["name"] == "Oregon Scientific Weather Sensor"


def test_match_rf_signature_new_batch_entry_matches() -> None:
    # Vevor profile (87/87) is unique in current table.
    match = match_rf_signature(short_pulse=87, long_pulse=87)
    assert match is not None
    assert match["name"] == "Vevor Wireless Weather Station 7-in-1"


def test_match_rf_signature_new_asymmetric_profile() -> None:
    # Efergy Optical has an uncommon 64/136 profile.
    match = match_rf_signature(short_pulse=64, long_pulse=136)
    assert match is not None
    assert match["name"] == "Efergy Optical"


def test_match_rf_signature_latest_batch_unique_profile() -> None:
    match = match_rf_signature(short_pulse=2500, long_pulse=5000)
    assert match is not None
    assert match["name"] == "Globaltronics GT-WT-02 Sensor"


def test_match_rf_signature_new_tfa_profile() -> None:
    match = match_rf_signature(short_pulse=200, long_pulse=320)
    assert match is not None
    assert match["name"] == "Revolt NC-5642 Energy Meter"


def test_match_rf_signature_norgo_profile() -> None:
    match = match_rf_signature(short_pulse=100, long_pulse=200)
    assert match is not None
    assert match["name"] == "Continental KR5V2X Car Remote (-f 313.8M -s 1024k)"


def test_match_rf_signature_silvercrest_profile() -> None:
    match = match_rf_signature(short_pulse=264, long_pulse=744)
    assert match is not None
    assert match["name"] == "Silvercrest Remote Control"


def test_match_rf_signature_auriol_profile() -> None:
    match = match_rf_signature(short_pulse=310, long_pulse=310)
    assert match is not None
    assert match["name"] == "Revolt ZX-7717 power meter"


def test_match_rf_signature_celsia_profile() -> None:
    match = match_rf_signature(short_pulse=708, long_pulse=1076)
    assert match is not None
    assert match["name"] == "Lock"


def test_match_rf_signature_burnhard_profile() -> None:
    match = match_rf_signature(short_pulse=604, long_pulse=604)
    assert match is not None
    assert match["name"] == "DirecTV RC66RX Remote Control"


def test_match_rf_signature_waveman_profile() -> None:
    match = match_rf_signature(short_pulse=357, long_pulse=1064)
    assert match is not None
    assert match["name"] == "Waveman Switch Transmitter"


def test_match_rf_signature_dish_profile() -> None:
    match = match_rf_signature(short_pulse=1692, long_pulse=2812)
    assert match is not None
    assert match["name"] == "Dish remote 6.3"


def test_match_rf_signature_tfa3196_profile() -> None:
    match = match_rf_signature(short_pulse=1600, long_pulse=1832)
    assert match is not None
    assert match["name"] == "Atech-WS308 temperature sensor"



def test_capture_to_signature_persists_user_signature(tmp_path, monkeypatch) -> None:
    user_db = tmp_path / "signatures_user.json"
    monkeypatch.setattr(signatures_data, "USER_SIGNATURES_FILE", user_db)

    created = capture_to_signature(
        name="Barieră Garaj Vecin",
        short_pulse=320,
        long_pulse=640,
        gap=1500,
        modulation="OOK/ASK",
    )

    assert created["name"] == "Barieră Garaj Vecin"
    assert user_db.exists()
    matched = match_rf_signature(short_pulse=320, long_pulse=640)
    assert matched is not None
    assert matched["name"] == "Barieră Garaj Vecin"
