from unittest.mock import MagicMock, patch

import pytest

from disasters import auth

TILE = (
    "https://archive.podaac.earthdata.nasa.gov/podaac-ops-cumulus-protected/"
    "OPERA_L3_DSWX-S1_V1/OPERA_L3_DSWx-S1_T11SQA_20261021T134211Z_B01_WTR.tif"
)
ASF = "https://datapool.asf.alaska.edu/RTC/OPERA-S1/OPERA_L2_RTC-S1_example_VV.tif"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for name in (
        auth.PODAAC_S3_ENV,
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "_PODAAC_S3", None)
    auth._PODAAC_STATE.update(creds=None, source=None, time=0.0, fs=None, gdal=None)
    yield
    auth._PODAAC_STATE.update(creds=None, source=None, time=0.0, fs=None, gdal=None)


def test_off_by_default_keeps_https():
    assert not auth.podaac_s3_enabled()
    assert auth.podaac_s3_location(TILE) is None
    assert auth.podaac_gdal_path(TILE) is None


def test_region_alone_does_not_switch_it_on(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert not auth.podaac_s3_enabled()


def test_env_variable_and_flag(monkeypatch):
    monkeypatch.setenv(auth.PODAAC_S3_ENV, "1")
    assert auth.podaac_s3_enabled()
    auth.set_podaac_s3(False)
    assert not auth.podaac_s3_enabled()
    monkeypatch.delenv(auth.PODAAC_S3_ENV)
    auth.set_podaac_s3(True)
    assert auth.podaac_s3_enabled()


def test_location_maps_only_podaac_links():
    auth.set_podaac_s3(True)
    bucket, key = auth.podaac_s3_location(TILE)
    assert bucket == "podaac-ops-cumulus-protected"
    assert key.startswith("OPERA_L3_DSWX-S1_V1/") and key.endswith("_B01_WTR.tif")
    assert auth.podaac_s3_location("/vsicurl/" + TILE) == (bucket, key)
    assert auth.podaac_s3_location(ASF) is None
    assert auth.podaac_s3_location("/data/local_tile.tif") is None


def test_credentials_from_environment(monkeypatch):
    auth.set_podaac_s3(True)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token")
    with patch.object(auth.earthaccess, "get_s3_credentials") as fetch:
        creds = auth._podaac_credentials()
    fetch.assert_not_called()
    assert creds["accessKeyId"] == "AKIDEXAMPLE"
    assert auth._PODAAC_STATE["source"] == "environment"


def test_credentials_from_earthdata_are_renewed(monkeypatch):
    auth.set_podaac_s3(True)
    fake = {"accessKeyId": "a", "secretAccessKey": "b", "sessionToken": "c"}
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    with (
        patch.object(auth.earthaccess, "get_s3_credentials", return_value=fake) as fetch,
        patch.object(auth.earthaccess, "login"),
        patch.object(auth.earthaccess, "__auth__", MagicMock(authenticated=True), create=True),
    ):
        auth._podaac_credentials()
        clock[0] += 10 * 60
        auth._podaac_credentials()
        assert fetch.call_count == 1
        clock[0] += auth.PODAAC_CREDENTIALS_MAX_AGE
        auth._podaac_credentials()
        assert fetch.call_count == 2
    fetch.assert_called_with(daac="PODAAC")


def test_gdal_credentials_are_scoped_to_the_podaac_bucket(monkeypatch):
    auth.set_podaac_s3(True)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    with patch.object(auth.gdal, "SetPathSpecificOption") as set_option, patch.object(
        auth.gdal, "SetConfigOption"
    ) as set_global:
        path = auth.podaac_gdal_path(TILE)
    assert path.startswith("/vsis3/podaac-ops-cumulus-protected/OPERA_L3_DSWX-S1_V1/")
    prefixes = {c.args[0] for c in set_option.call_args_list}
    assert prefixes == {"/vsis3/podaac-ops-cumulus-protected/"}
    set_global.assert_not_called()


def test_open_granule_falls_back_to_https():
    with patch.object(auth, "open_file") as open_file:
        auth.open_granule(TILE, "user", "pass")
    open_file.assert_called_once_with(
        TILE, earthdata_username="user", earthdata_password="pass"
    )
