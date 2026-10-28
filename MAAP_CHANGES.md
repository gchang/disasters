# Changes from upstream

Upstream: https://github.com/OPERA-Cal-Val/disasters, branch `main` at commit 2c49a0ea4735c813cff0233cb4225621decf4248 (2026-10-21; the repository has no tags), by the OPERA Cal/Val team with the NASA Disasters program. Apache License 2.0 (`LICENSE`).

This copy starts from upstream's file tree at 2c49a0e without its git history. Upstream has no `.github/` folder, so nothing was left out. The workflow code under `src/disasters/`, the tests and the LICENSE are untouched; only the environment files and the README changed, as listed below. Results do not change.

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

One thing to know when running inside AWS us-west-2: PO.DAAC's HTTPS download endpoint answers there with a presigned in-region S3 address that returns 403 AccessDenied, so the DSWx-HLS and DSWx-S1 tiles cannot be streamed over HTTPS (`opera_utils.disp._remote.open_file`, used in `mosaic.py` and `pipeline.py`). For this check those reads went to PO.DAAC by direct S3 with its temporary credentials (`https://archive.podaac.earthdata.nasa.gov/s3credentials`). Outside AWS, the HTTPS route works unchanged. The code here was not changed for this.
