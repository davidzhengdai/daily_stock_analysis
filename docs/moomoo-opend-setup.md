# Moomoo OpenD Setup Guide

This guide explains how to enable Moomoo as a market data source for local or Docker deployments.

Moomoo OpenAPI requires two parts:

- `moomoo-api`: the Python SDK installed inside this project or Docker image.
- Moomoo OpenD: a separate gateway process that must be running and logged in on the host machine.

The application connects to OpenD through `MOOMOO_OPEND_HOST` and `MOOMOO_OPEND_PORT`. Without OpenD, Moomoo quotes cannot work even if `moomoo-api` is installed.

## What Moomoo Provides

The current integration uses Moomoo for:

- US/HK/A-share realtime quote snapshots when permission is available.
- US realtime fields such as price, change percent, volume, amount, volume ratio, turnover rate, PE/PB, market value, and 52-week high/low.
- US daily candles through OpenD historical K-line APIs when Moomoo is first in the realtime source priority.

Moomoo news is not wired into the news pipeline. News still comes from the configured search providers, such as SerpAPI, Tavily, Brave, Bocha, MiniMax, or SearXNG.

## 1. Download OpenD On Ubuntu

Use the OpenD tarball from the Moomoo OpenAPI download page. The Ubuntu package name may mention `Ubuntu18.04`; that is the build baseline and can still work on newer Ubuntu versions.

Stable OpenD example:

```bash
mkdir -p ~/apps/moomoo-opend
cd ~/apps/moomoo-opend

curl -L \
  -o moomoo_OpenD_10.4.6408_Ubuntu18.04.tar.gz \
  "https://softwaredownload.futustatic.com/moomoo_OpenD_10.4.6408_Ubuntu18.04.tar.gz"

tar -xzf moomoo_OpenD_10.4.6408_Ubuntu18.04.tar.gz
```

Find the executable:

```bash
find . -maxdepth 4 -type f \( -name "OpenD" -o -name "FutuOpenD" \)
```

If Moomoo publishes a newer OpenD version, use the Linux/Ubuntu OpenD URL from:

```text
https://www.moomoo.com/download/OpenAPI
```

Do not use the normal Moomoo Linux desktop `.deb` for this integration; the app needs OpenD.

## 2. Configure OpenD

Enter the extracted OpenD directory and edit `OpenD.xml`:

```bash
cd ~/apps/moomoo-opend/moomoo_OpenD_10.4.6408_Ubuntu18.04/moomoo_OpenD_10.4.6408_Ubuntu18.04
nano OpenD.xml
```

Set the API port to `11111`.

For non-Docker local Python runs, `127.0.0.1` is enough:

```text
API listening IP: 127.0.0.1
API port: 11111
```

For Docker deployments, OpenD must listen on an address the container can reach. Use:

```text
API listening IP: 0.0.0.0
API port: 11111
```

Also configure the login account/password fields according to the OpenD config file format. If login fails with `Password does not match`, OpenD exits and port `11111` will not stay open.

## 3. Start OpenD

Start the binary from the extracted OpenD directory:

```bash
chmod +x ./OpenD
./OpenD
```

or, if the package uses the other binary name:

```bash
chmod +x ./FutuOpenD
./FutuOpenD
```

Successful startup should show:

```text
Login successful
Required data is ready
API Listening Address: 0.0.0.0:11111
```

Keep this process running while the stock analysis service is running.

## 4. Configure The App

For Docker, set these values in `.env`:

```env
REALTIME_SOURCE_PRIORITY=moomoo
MOOMOO_OPEND_HOST=host.docker.internal
MOOMOO_OPEND_PORT=11111
MOOMOO_OPEND_CONNECT_TIMEOUT=1.0
MOOMOO_EXTENDED_TIME=false
```

For a direct host Python run, use:

```env
REALTIME_SOURCE_PRIORITY=moomoo
MOOMOO_OPEND_HOST=127.0.0.1
MOOMOO_OPEND_PORT=11111
MOOMOO_OPEND_CONNECT_TIMEOUT=1.0
MOOMOO_EXTENDED_TIME=false
```

If you want fallback sources after Moomoo fails, use a comma-separated priority list:

```env
REALTIME_SOURCE_PRIORITY=moomoo,tencent,akshare_sina,efinance,akshare_em
```

If you want Moomoo only, leave only:

```env
REALTIME_SOURCE_PRIORITY=moomoo
```

After changing `.env`, rebuild/restart Docker:

```bash
scripts/docker-build-launch.sh server --no-cache
```

## 5. Verify OpenD

Verify OpenD is listening on the host:

```bash
nc -vz 127.0.0.1 11111
```

Expected result:

```text
succeeded
```

Verify Docker can reach OpenD:

```bash
docker compose -f docker/docker-compose.yml exec -T server python - <<'PY'
import socket
socket.create_connection(("host.docker.internal", 11111), timeout=3).close()
print("OpenD reachable")
PY
```

Expected result:

```text
OpenD reachable
```

Verify the application can retrieve a Moomoo quote:

```bash
docker compose -f docker/docker-compose.yml exec -T server python - <<'PY'
from data_provider.base import DataFetcherManager

q = DataFetcherManager().get_realtime_quote("AMZN")
print(q.to_dict() if q else None)
PY
```

Expected result includes:

```text
'source': 'moomoo'
```

## 6. Verify In The Web UI

Run a US stock analysis, for example `AMZN`, then inspect the raw analysis data.

Moomoo is working when you see:

```json
"realtimeQuoteRaw": {
  "source": "moomoo",
  "price": 272.68,
  "peRatio": 32.617,
  "pbRatio": 6.637
}
```

The `today.dataSource` field may still show `YfinanceFetcher` if a historical daily row already existed in the database and the pipeline reused the cached row. That does not mean realtime Moomoo failed. Check `realtimeQuoteRaw.source` for realtime quote provenance.

## Troubleshooting

`ConnectionRefusedError: [Errno 111] Connection refused`

OpenD is not listening at the configured host/port. Start OpenD, log in successfully, and confirm `nc -vz 127.0.0.1 11111` passes.

`Docker can resolve host.docker.internal but still gets connection refused`

OpenD is probably listening only on `127.0.0.1`. Change the OpenD API listening IP to `0.0.0.0`, restart OpenD, and retry the Docker socket check.

`Login failed, Password does not match`

Fix the OpenD account/password configuration. OpenD exits after login failure, so the app cannot connect.

`source` is `moomoo`, but news is empty or says `SearXNG`

This is expected. Moomoo news is not currently integrated. Configure a search provider if news/catalyst coverage is required.

`Tushare Token 未配置，此数据源不可用`

This warning is unrelated to Moomoo. It only means Tushare is unavailable. Moomoo can still work if `realtimeQuoteRaw.source` is `moomoo`.
