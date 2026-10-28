# Changes from upstream

Upstream: https://github.com/OPERA-Cal-Val/disasters, branch `main` at commit 2c49a0ea4735c813cff0233cb4225621decf4248 (2026-10-21; the repository has no tags), by the OPERA Cal/Val team with the NASA Disasters program. Apache License 2.0 (`LICENSE`).

This copy starts from upstream's file tree at 2c49a0e without its git history. Upstream has no `.github/` folder, so nothing was left out. The LICENSE and the existing tests are untouched. The environment files and the README changed, and the workflow code gained one opt-in option for reading PO.DAAC tiles from S3 inside AWS us-west-2, as listed below. Results do not change.

## 2026-10-28: environment.yml pins GMT to a build made for GDAL 3.12

`environment.yml` pins `gdal=3.12` and `libgdal=3.12` and also asks for `pygmt`. The newest conda-forge GMT builds (6.6.0 builds 8 and 9, from May 2026 on) need GDAL 3.13, so the solver backtracks for a very long time looking for a GMT that fits. On Linux, `conda env create -f environment.yml` with conda 26.9.4 (libmamba) ran past 15 minutes twice without finishing, and an automated build with a 60-minute limit timed out in the same step.

`environment.yml` now also lists `gmt=6.6.0=*_7`. Build 7 is the newest GMT 6.6.0 built against GDAL 3.12 (it needs libgdal-core 3.12.3 or newer within 3.12) and exists for linux-64, osx-64 and osx-arm64, so the line does not tie the file to one platform. With it, the file solves on Linux in about 8 minutes, and the whole `conda env create` took 9 min 43 s with 4.3 GB peak memory (4 cores). The GDAL pin itself is unchanged: the environment still gets GDAL 3.12.4.

## 2026-10-28: an explicit conda lock for Linux

`conda-linux-64.lock` is the fixed `environment.yml` solved on linux-64 on 2026-10-28 and written out with `conda list --explicit --md5`: 554 conda-forge packages with their URLs and MD5 sums (Python 3.12.16, GDAL 3.12.4, GMT 6.6.0 build 7, pygmt 0.19.0, opera-utils 0.25.3, numpy 2.5.4, rasterio 1.5.0). Creating the environment from it needs no solver step. The README has a new section on installing from it:

```bash
conda create -n disasters --file conda-linux-64.lock
conda activate disasters
pip install "next_pass @ git+https://github.com/OPERA-Cal-Val/next_pass.git@99a55e644caa21b64931c49c48c4def8adf4c3ff"
pip install .
```

`next_pass` is installed from GitHub at commit 99a55e6 (2026-10-12), which is what `@main` in `environment.yml` and `requirements.txt` resolved to when the lock was made; `pip install .` keeps that version. It brings 13 more packages from PyPI that are not in the lock (rich, timezonefinder, yagmail and their dependencies). `environment.yml` stays the readable list of dependencies and the route for other platforms. To refresh the lock after changing it, create an environment from `environment.yml` and run `conda list --explicit --md5 > conda-linux-64.lock` in it.

## Check

On 2026-10-28, on Linux x86_64 (an EC2 machine in AWS us-west-2, 4 cores), from a fresh `git clone` of this repository:

- `conda create --file conda-linux-64.lock` took 1 min 45 s, downloading all 554 packages into an empty package cache (0.3 GB peak memory). The two `pip install` steps took 7 s and 5 s; `pip check` found no broken requirements, and `next_pass` stayed at 99a55e6.
- `pytest tests` (pytest added only for this): 19 passed, 1 failed, the same as with upstream's environment. The failure is in the test, not the package: `tests/test_pipeline.py::test_run_slope_filter_only_processes_nested_tifs` patches `authenticate` without a return value, so the code under test unpacks `None`.
- The README's Lake Mead flood example over a smaller box, with Earthdata credentials in `~/.netrc`:

  ```bash
  opera-disaster run -b "35.9 36.6 -115.0 -114.0" -d 2026-10-21 -n 2 -m flood -o LakeMead -lt "Lake Mead Floods"
  ```

  The flood products were done 9 minutes after the start: 25 GeoTIFF mosaics (DSWx-HLS and DSWx-S1 WTR, BWTR and CONF for two passes each, the HLS bands and Fmask, water-gain and maximum-extent layers), 14 map PNGs and 14 PDF layouts, about 100 MB, plus the next_pass product table. Peak resident memory was 14.0 GB, measured in a second run that was stopped after 14 minutes and wrote a byte-identical DSWx-S1 mosaic; plan for at least 16 GB. The last step, counting OpenStreetMap buildings in the flooded area through the public Overpass API, takes 1 to 1.5 minutes per grid window here and was stopped at the one-hour mark in window 39 of 42, so the building-count table was not written. That step depends on the Overpass service and does not change the flood products.

- The DSWx-S1 WTR mosaic for the 2026-10-21 13:42 descending pass (UTM 11N, 3064 x 2667 pixels at 30 m) was compared with the 6 official DSWx-S1 tiles it was built from, each warped onto the mosaic grid with nearest neighbor: 99.973% of 13,294,932 overlapping pixels are identical. All 3,582 differing pixels lie where two or more tiles overlap, where the mosaic keeps the first valid tile; where only one tile covers a pixel there are no differences. This is the same result as with upstream's environment on 2026-10-24.

Inside AWS us-west-2 the PO.DAAC tiles could not be read over HTTPS; the next section adds an option for that. In this check those reads went to PO.DAAC by direct S3 through a wrapper outside the code.

## 2026-10-28: an option to read PO.DAAC tiles from S3 inside AWS us-west-2

Why: the DSWx-HLS and DSWx-S1 tiles come from PO.DAAC. From inside AWS us-west-2 (where NASA MAAP runs), PO.DAAC answers an HTTPS download of these tiles with a redirect to an in-region S3 address that refuses the read with 403 Forbidden, while a HEAD request still succeeds. So `run` fails there at the first PO.DAAC tile. The same file can be read directly from PO.DAAC's bucket with PO.DAAC's temporary S3 credentials (`https://archive.podaac.earthdata.nasa.gov/s3credentials`), and the HTTPS link maps one to one onto the bucket: `https://archive.podaac.earthdata.nasa.gov/<bucket>/<key>` is `s3://<bucket>/<key>`.

What changed (one commit each):

- `src/disasters/auth.py`: an opt-in, `DISASTERS_PODAAC_S3=1` or `set_podaac_s3(True)`, off by default. `open_granule()` opens PO.DAAC links from S3 (s3fs) and every other link through opera_utils' `open_file` exactly as before; `podaac_gdal_path()` gives `/vsis3/<bucket>/<key>` for GDAL reads. PO.DAAC credentials are taken from `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` when those are set (so a platform can supply them), and otherwise fetched from PO.DAAC with the Earthdata login through `earthaccess.get_s3_credentials(daac="PODAAC")` and fetched again after 45 minutes, before the one-hour credentials expire. GDAL gets them as path-specific options for the PO.DAAC bucket only, so the ASF credentials that `authenticate()` sets for all other S3 reads stay in place, and the two sets never overwrite each other. No instance metadata lookup is added. When `AWS_REGION` or `AWS_DEFAULT_REGION` is `us-west-2` and the option is off, the log prints a hint; the option is not switched on by itself. `s3fs` (already installed as an earthaccess dependency) is now listed in `environment.yml` and `requirements.txt` because auth.py imports it; the solved environment is the same, so `conda-linux-64.lock` is unchanged.
- Every PO.DAAC read goes through these helpers: the tile loads and the warp source in `mosaic.py`, the `download` command and the GDAL direct-to-disk mosaic and bounds reads in `pipeline.py`, the orbit-direction read in `catalog.py`, the B10 DEM warp in `filters.py` (GDAL `/vsicurl` before), and the DEM fetcher in `catalog.py` (which copies the DEM files itself under the option, because `earthaccess.download` would use the HTTPS links).
- `src/disasters/cli.py`: `--podaac-s3` on `run`, `download` and `slope-filter`, the same as setting the environment variable.
- `tests/test_podaac_s3.py`: 8 tests (off by default, also with `AWS_REGION=us-west-2`; the variable and the flag; link mapping; credentials from the environment and from PO.DAAC with renewal; GDAL options scoped to the PO.DAAC bucket; the HTTPS fallback).
- README: a section "Running inside AWS us-west-2 (for example NASA MAAP)" and the flag in the argument table.

Without the option, nothing changes: the same HTTPS reads as before.

Check, on 2026-10-28, on an EC2 machine in AWS us-west-2 (so with the same PO.DAAC behavior as MAAP), 4 cores and 16 GB, from a fresh `git clone` of this repository, environment from `conda-linux-64.lock` (1 min 26 s) plus the two `pip install` steps; `pip check` clean; Earthdata login in `~/.netrc`, no AWS variables set:

- `pytest tests`: 27 passed, 1 failed (the known test bug described above).
- `opera-disaster run -b "36 36.5 -114.5 -114" -m flood -d 2026-10-21 -n 2 -o LakeMead -lt "Lake Mead Floods" --functionality opera_search --podaac-s3`: the log shows one PO.DAAC credential request and 60 tile reads from `s3://podaac-ops-cumulus-protected/` (WTR, BWTR and CONF of DSWx-HLS and DSWx-S1), no warnings or errors. The flood products (25 GeoTIFFs, 14 map PNGs, 14 PDF layouts) were done 6 minutes after the start. The OpenStreetMap building-count step then ran its first pass (no buildings in the flooded areas) and was stopped at 54 minutes in window 14 of 16 of its second pass; it only queries the public Overpass API and does not change the flood products. Peak memory 13.2 GB.
- The DSWx-S1 WTR mosaic of the 2026-10-21 13:42 descending pass (UTM 12N, 1561 x 1900 pixels) against the 4 official tiles it was built from, each warped onto the mosaic grid with nearest neighbor: 99.976% of 5,795,081 overlapping pixels are identical. All 1,379 differing pixels lie where two tiles overlap (the mosaic keeps the first valid tile); there are none where a single tile covers.
- The same command without `--podaac-s3` still reads over HTTPS and stops at the first PO.DAAC tile with 403 Forbidden from the in-region S3 redirect, as before this change.
