import atexit
import logging
import netrc
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import boto3
import earthaccess
import rasterio
from opera_utils.disp._remote import open_file
from osgeo import gdal
from rasterio.session import AWSSession

logger = logging.getLogger(__name__)

_RIO_ENV = None
_RIO_ENV_SIGNATURE = None


def _close_rasterio_env() -> None:
    """Close the cached Rasterio environment when it is replaced or on exit."""
    global _RIO_ENV, _RIO_ENV_SIGNATURE

    if _RIO_ENV is None:
        return

    try:
        _RIO_ENV.__exit__(None, None, None)
    except Exception:
        pass
    finally:
        _RIO_ENV = None
        _RIO_ENV_SIGNATURE = None


def authenticate() -> tuple[str, str]:
    """
    Authenticate with Earthdata (EDL) and set up Rasterio/GDAL environment.
    Returns (username, password) from ~/.netrc for endpoints that need it.
    """

    # Ensure Earthdata session (creates/updates ~/.netrc)
    try:
        earthaccess.login(strategy="netrc")
    except Exception as e:
        print(f"[WARNING] earthaccess.login() failed; continuing: {e}")

    # Use a hidden cookie file in the home directory to avoid local permission issues
    cookie_path = str(Path.home() / ".earthdata_cookies.txt")

    # Try to obtain S3 temporary creds for ASF (OPERA RTC Host)
    temp_creds_req = None
    try:
        temp_creds_req = earthaccess.get_s3_credentials(daac="ASF")
    except Exception as e:
        print(
            f"[WARNING] Could not get S3 credentials; continuing without AWS session: {e}"
        )

    # Setup GLOBAL GDAL configuration (For raw osgeo.gdal tools like Warp)
    gdal.SetConfigOption("GDAL_HTTP_COOKIEFILE", cookie_path)
    gdal.SetConfigOption("GDAL_HTTP_COOKIEJAR", cookie_path)
    gdal.SetConfigOption("GDAL_HTTP_NETRC", "YES")
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", "tif,tiff,xml,json")

    # Build Rasterio environment (For xarray/rioxarray tools)
    env_kwargs = dict(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS="tif,tiff,xml,json",
        GDAL_HTTP_COOKIEFILE=cookie_path,
        GDAL_HTTP_COOKIEJAR=cookie_path,
    )

    env_signature = (
        (
            cookie_path,
            temp_creds_req["accessKeyId"],
            temp_creds_req["secretAccessKey"],
            temp_creds_req["sessionToken"],
        )
        if isinstance(temp_creds_req, dict)
        and all(
            k in temp_creds_req
            for k in ("accessKeyId", "secretAccessKey", "sessionToken")
        )
        else (
            cookie_path,
            None,
            None,
            None,
        )
    )

    if isinstance(temp_creds_req, dict) and all(
        k in temp_creds_req for k in ("accessKeyId", "secretAccessKey", "sessionToken")
    ):
        # Apply AWS Creds to Rasterio
        session = boto3.Session(
            aws_access_key_id=temp_creds_req["accessKeyId"],
            aws_secret_access_key=temp_creds_req["secretAccessKey"],
            aws_session_token=temp_creds_req["sessionToken"],
            region_name="us-west-2",
        )
        rio_env = rasterio.Env(AWSSession(session), **env_kwargs)

        # Apply AWS Creds to raw GDAL
        gdal.SetConfigOption("AWS_ACCESS_KEY_ID", temp_creds_req["accessKeyId"])
        gdal.SetConfigOption("AWS_SECRET_ACCESS_KEY", temp_creds_req["secretAccessKey"])
        gdal.SetConfigOption("AWS_SESSION_TOKEN", temp_creds_req["sessionToken"])
        gdal.SetConfigOption("AWS_REGION", "us-west-2")
    else:
        if temp_creds_req is not None:
            print(
                "[WARNING] S3 credentials missing expected keys; continuing without AWS session."
            )
        rio_env = rasterio.Env(**env_kwargs)

    # Keep one Rasterio env open per process and refresh it only when credentials change.
    global _RIO_ENV, _RIO_ENV_SIGNATURE
    if _RIO_ENV_SIGNATURE != env_signature:
        _close_rasterio_env()
        rio_env.__enter__()
        _RIO_ENV = rio_env
        _RIO_ENV_SIGNATURE = env_signature

    # Parse credentials from ~/.netrc (used for xarray endpoints)
    netrc_file = Path.home() / ".netrc"
    try:
        auths = netrc.netrc(netrc_file)
        username, _, password = auths.authenticators("urs.earthdata.nasa.gov")
    except Exception:
        print("[WARNING] Could not parse ~/.netrc file.")
        username, password = None, None

    if not podaac_s3_enabled() and _aws_region() == "us-west-2":
        logger.info(
            "AWS region is us-west-2. If PO.DAAC downloads fail with 403, rerun with "
            f"--podaac-s3 (or {PODAAC_S3_ENV}=1) to read PO.DAAC granules directly from S3."
        )

    return username, password


# --- Reading PO.DAAC granules directly from S3 (opt-in) ---
#
# Inside AWS us-west-2 (for example on NASA MAAP), PO.DAAC answers HTTPS downloads of its
# protected granules with a redirect to an in-region S3 address that refuses the read (403),
# so the DSWx-HLS and DSWx-S1 tiles cannot be streamed over HTTPS there. The same granule can
# be read from S3 with PO.DAAC's temporary credentials: the HTTPS link
# https://archive.podaac.earthdata.nasa.gov/<bucket>/<key> is s3://<bucket>/<key>.
# This only works from us-west-2, so it is off unless asked for with --podaac-s3 or
# DISASTERS_PODAAC_S3=1. Everything else (ASF, LP DAAC) is read as before.

PODAAC_S3_ENV = "DISASTERS_PODAAC_S3"
PODAAC_HOST = "archive.podaac.earthdata.nasa.gov"
PODAAC_S3_CREDENTIALS_URL = f"https://{PODAAC_HOST}/s3credentials"
# DAAC temporary S3 credentials last one hour; get new ones well before that.
PODAAC_CREDENTIALS_MAX_AGE = 45 * 60

_PODAAC_S3 = None  # set by --podaac-s3; None means "use the environment variable"
_PODAAC_LOCK = threading.Lock()
_PODAAC_STATE = {"creds": None, "source": None, "time": 0.0, "fs": None, "gdal": None}


def _aws_region() -> str | None:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def set_podaac_s3(enabled: bool) -> None:
    """Turn direct S3 reading of PO.DAAC granules on or off for this process."""
    global _PODAAC_S3
    _PODAAC_S3 = bool(enabled)


def podaac_s3_enabled() -> bool:
    """Whether PO.DAAC granules are read from S3 (--podaac-s3 or DISASTERS_PODAAC_S3=1)."""
    if _PODAAC_S3 is not None:
        return _PODAAC_S3
    value = os.environ.get(PODAAC_S3_ENV, "").strip().lower()
    return value in ("1", "true", "yes", "on")


def podaac_s3_location(url) -> tuple[str, str] | None:
    """
    (bucket, key) for a PO.DAAC HTTPS link when direct S3 reading is on, else None.
    A "/vsicurl/" prefix on the link is accepted.
    """
    if not podaac_s3_enabled():
        return None
    url = str(url)
    if url.startswith("/vsicurl/"):
        url = url[len("/vsicurl/") :]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != PODAAC_HOST:
        return None
    bucket, _, key = parsed.path.lstrip("/").partition("/")
    if not bucket or not key or bucket == "s3credentials":
        return None
    return bucket, key


def _podaac_credentials() -> dict:
    """
    PO.DAAC S3 credentials. If AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (and
    AWS_SESSION_TOKEN for temporary ones) are set, those are used as they are, so a platform
    can supply them. Otherwise they are fetched from PO.DAAC with the Earthdata login
    (earthaccess, ~/.netrc) and fetched again after 45 minutes. Call with _PODAAC_LOCK held.
    """
    state = _PODAAC_STATE
    key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if key_id and secret:
        creds = {
            "accessKeyId": key_id,
            "secretAccessKey": secret,
            "sessionToken": os.environ.get("AWS_SESSION_TOKEN"),
        }
        if state["creds"] != creds:
            state.update(creds=creds, source="environment", time=time.monotonic())
            state.update(fs=None, gdal=None)
            logger.info(
                "Reading PO.DAAC granules from S3 with the credentials in AWS_ACCESS_KEY_ID."
            )
        return creds

    if (
        state["source"] == "earthdata"
        and time.monotonic() - state["time"] < PODAAC_CREDENTIALS_MAX_AGE
    ):
        return state["creds"]

    if not earthaccess.__auth__.authenticated:
        earthaccess.login(strategy="netrc")
    creds = earthaccess.get_s3_credentials(daac="PODAAC")
    if not isinstance(creds, dict) or not all(
        creds.get(k) for k in ("accessKeyId", "secretAccessKey", "sessionToken")
    ):
        raise RuntimeError(
            f"Could not get S3 credentials from {PODAAC_S3_CREDENTIALS_URL}; "
            "check the Earthdata login in ~/.netrc."
        )
    creds = {k: creds[k] for k in ("accessKeyId", "secretAccessKey", "sessionToken")}
    renewed = state["source"] == "earthdata"
    state.update(creds=creds, source="earthdata", time=time.monotonic(), fs=None, gdal=None)
    logger.info(
        f"{'Renewed' if renewed else 'Got'} PO.DAAC S3 credentials "
        "(Earthdata login); reading PO.DAAC granules from S3."
    )
    return creds


def _podaac_filesystem():
    """s3fs filesystem for the PO.DAAC buckets, rebuilt whenever the credentials change."""
    import s3fs

    with _PODAAC_LOCK:
        creds = _podaac_credentials()
        if _PODAAC_STATE["fs"] is None:
            _PODAAC_STATE["fs"] = s3fs.S3FileSystem(
                key=creds["accessKeyId"],
                secret=creds["secretAccessKey"],
                token=creds["sessionToken"],
                client_kwargs={"region_name": "us-west-2"},
                skip_instance_cache=True,
            )
        return _PODAAC_STATE["fs"]


def open_granule(
    url,
    earthdata_username: str | None = None,
    earthdata_password: str | None = None,
):
    """
    Open a remote granule for reading.

    With --podaac-s3, PO.DAAC links are opened from s3://<bucket>/<key> with PO.DAAC
    credentials. Everything else goes through opera_utils' open_file over HTTPS with the
    Earthdata login, as before.
    """
    location = podaac_s3_location(url)
    if location is not None:
        bucket, key = location
        logger.debug(f"Reading s3://{bucket}/{key}")
        return _podaac_filesystem().open(f"{bucket}/{key}", mode="rb")
    return open_file(
        url, earthdata_username=earthdata_username, earthdata_password=earthdata_password
    )


def podaac_gdal_path(url) -> str | None:
    """
    GDAL path /vsis3/<bucket>/<key> for a PO.DAAC link when direct S3 reading is on, else None.

    The PO.DAAC credentials are set as GDAL options for the PO.DAAC bucket only, so the ASF
    credentials that authenticate() sets for all other S3 reads stay in place.
    """
    location = podaac_s3_location(url)
    if location is None:
        return None
    bucket, key = location
    with _PODAAC_LOCK:
        creds = _podaac_credentials()
        configured = _PODAAC_STATE["gdal"] or set()
        if bucket not in configured:
            prefix = f"/vsis3/{bucket}/"
            gdal.SetPathSpecificOption(prefix, "AWS_ACCESS_KEY_ID", creds["accessKeyId"])
            gdal.SetPathSpecificOption(
                prefix, "AWS_SECRET_ACCESS_KEY", creds["secretAccessKey"]
            )
            gdal.SetPathSpecificOption(
                prefix, "AWS_SESSION_TOKEN", creds["sessionToken"] or ""
            )
            gdal.SetPathSpecificOption(prefix, "AWS_REGION", "us-west-2")
            configured.add(bucket)
            _PODAAC_STATE["gdal"] = configured
    return f"/vsis3/{bucket}/{key}"


atexit.register(_close_rasterio_env)
