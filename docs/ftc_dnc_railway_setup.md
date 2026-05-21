# FTC DNC Registry — Railway Setup

The pipeline scrubs SearchBug-sourced phones against a self-hosted snapshot
of the FTC's telemarketing.donotcall.gov registry. Coverage is licensed by
area code; phones from area codes we haven't downloaded stay flagged
`dnc_status="unknown"` and are held by the DNC gate (no GHL push, no Bland
call).

## Refreshing the dataset

1. Log into telemarketing.donotcall.gov and re-download the .txt file for each
   subscribed area code into a local directory (e.g. `~/Downloads/dnc`).
2. Build the SQLite database:

   ```bash
   python scripts/build_dnc_sqlite.py \
     --input-dir ~/Downloads/dnc \
     --output dnc.db
   ```

   Expected output: `Done in ~5s — 6.3M unique phones across 5 area codes (~50 MB) → dnc.db`.

## Deploying to Railway

The database lives on a persistent volume so we don't bloat the repo (76 MB
of raw .txt per refresh) and so Railway redeploys don't lose it.

1. **Create the volume (once)** via the Railway dashboard or CLI:

   ```bash
   railway volume create dnc --mount /data/dnc
   ```

2. **Upload the SQLite file** to the volume:

   ```bash
   railway run --service leadgen "cp $(pwd)/dnc.db /data/dnc/dnc.db"
   ```

   (or use `railway link` + `scp` if SSH is enabled.)

3. **Set the env var** (once):

   ```bash
   railway variables set FTC_DNC_DB_PATH=/data/dnc/dnc.db --service leadgen
   ```

4. **Redeploy** to pick up the env var. The next startup logs will include
   `FTC DNC registry loaded from /data/dnc/dnc.db (area codes: 281,615,713,812,832)`.

## Behavior in the pipeline

For every contact with a phone whose `dnc_status` is `"unknown"`:

- If the area code is loaded **and** the phone is in the registry → status
  upgraded to `"blocked"`, contact held by DNC gate.
- If the area code is loaded **and** the phone is not found → status
  upgraded to `"clear"`, contact pushed to GHL and Instantly.
- If the area code is not loaded → status stays `"unknown"`, contact held
  by DNC gate (compliance hold pending area-code subscription).

The upgrade is persisted to Supabase so dashboard views reflect the result.

## Adding an area code

Subscribing to a new area code on telemarketing.donotcall.gov is $82/year.
After paying:

1. Download the new .txt file into the same directory.
2. Re-run `scripts/build_dnc_sqlite.py` and re-upload `dnc.db` to the volume.
3. No code change or redeploy required — the registry reloads on next app
   restart, but a restart is needed to pick up the new file. Trigger one via
   `railway redeploy --service leadgen`.
