# Redmine QBO Label Agent

The print agent for [redmine_qbo_labels](https://github.com/rickbarrette/redmine_qbo_labels): runs on a computer on the label printer's network, asks Redmine for labels every couple of seconds, and prints them on a Brother QL printer (made for the QL-820NWB with a 62mm continuous roll).

Redmine never needs to reach the printer, or the shop's network: the agent only makes outgoing HTTPS requests, so there's nothing to open on the router, and labels print from anywhere Redmine can be reached.

* **Finds the printer by itself** on the network (Bonjour/mDNS, which Brother printers announce), and again if its address changes. Or give it the address with `PRINTER`.
* **Checks the printer** before each label and every minute (cover open, no labels, wrong roll), and tells Redmine, which shows it on the Label printers page and in the print dialog.
* **Draws the label** the same way as the print dialog's preview: the lines stacked in the biggest bold letters that fit, on a label only as long as the text needs.
* Keeps one connection to Redmine open, so Redmine's name is looked up once instead of on every poll.

## Running it

In Redmine, **Administration » Label printers » New label printer** shows the agent's token and a Compose file with it filled in. On the computer on the printer's network:

```yaml
services:
  label-agent:
    image: barrettefab/redmine-qbo-label-agent:latest
    restart: always
    # Host networking lets the agent find the printer on the local network (Bonjour/mDNS)
    network_mode: host
    environment:
      REDMINE_URL: https://redmine.example.com
      AGENT_TOKEN: qbl_paste-the-token-here
      PRINTER_MODEL: QL-820NWB
      # Optional: skip the network search and use this printer
      # PRINTER: tcp://192.168.0.100
      LOG_LEVEL: INFO
```

```
docker compose up -d
docker compose logs -f
```

The image is on Docker Hub ([barrettefab/redmine-qbo-label-agent](https://hub.docker.com/r/barrettefab/redmine-qbo-label-agent)), so nothing is built on the shop's computer. It runs on PCs and on Raspberry Pis (64-bit or 32-bit Raspberry Pi OS, e.g. a Pi 3). To pin a version instead of `latest`, use its tag, e.g. `barrettefab/redmine-qbo-label-agent:2026.11.4`.

The log says which printer it found (`Found QL-820NWB at 192.168.0.100:9100`) and each label it prints.

Host networking is what lets it hear the printer's announcements. Without it (e.g. Docker Desktop on Windows or Mac), set `PRINTER` to the printer's address, preferably one reserved for it on the router.

It replaces brother_ql_web: both send to the printer's port 9100, and only one can print at a time, so they can run side by side, but there's no need to.

## Receipt printers (Star)

The agent also prints on a Star TSP100III receipt printer (e.g. TSP143IIILAN, 80mm): the pictures Redmine plugins send it, e.g. [redmine_qbo_tickets](https://github.com/rickbarrette/redmine_qbo_tickets)' key tickets. Add the printer in Redmine as a *Receipt printer*, and run an agent for it with:

```yaml
services:
  receipt-agent:
    image: barrettefab/redmine-qbo-label-agent:latest
    restart: always
    network_mode: host
    environment:
      REDMINE_URL: https://redmine.example.com
      AGENT_TOKEN: qbl_paste-the-receipt-printers-token-here
      PRINTER_TYPE: star
      PRINTER: tcp://192.168.0.101
      CUT: full
```

It isn't looked for on the network: give its address in `PRINTER` (reserve one for it on the router). Before each picture it asks the printer for its status (cover open, out of paper, cutter error, head too hot), and after it, waits for the printer to say everything printed. The pictures are printed as they are (576 dots wide, in Star's raster mode) and cut off.

One agent prints on one printer: with a label printer and a receipt printer, put both services in one Compose file.

## Updating

```
docker compose pull
docker compose up -d
```

The log's first line names the version that's running (`Print agent 2026.11.1 for https://...`), and Redmine's Label printers page shows each agent's version.

## Settings

Variable|Default|
---|---|---
`REDMINE_URL`||Redmine's address (required)
`AGENT_TOKEN`||The printer's token from **Administration » Label printers** (required)
`PRINTER_TYPE`|`brother_ql`|`brother_ql` (label printer) or `star` (receipt printer)
`PRINTER`|found on the network|The printer, e.g. `tcp://192.168.0.100` or `192.168.0.100:9100` (required for a Star)
`PRINTER_MODEL`|`QL-820NWB` (Star: `TSP143IIILAN`)|The model, as [brother_ql](https://github.com/inventree/brother_ql) names it; also picks this model when the network has several printers
`LABEL`|`62`|The roll, as brother_ql names it (`62`: 62mm continuous)
`CUT`|`full`|Star: `full`, `partial` or `none` (feed only)
`LOG_LEVEL`|`INFO`|`DEBUG` logs every poll and the printer's status messages; `WARNING` only problems
`VERIFY_SSL`|`1`|`0` to skip checking Redmine's certificate

How often it asks for labels, and how long labels are, are set in Redmine (the plugin's settings) and picked up within a poll.

## Publishing

`./publish.sh` runs the tests, builds the image and pushes it to Docker Hub tagged with its version (`__version__` in `label_agent/__init__.py`) and `latest`. It only publishes a committed version whose tag is on HEAD: bump the version, commit, `git tag <version>`, push, then publish.

## Development

```
pip install -r requirements-dev.txt
python -m pytest
```

The tests draw labels and print them on a fake printer, through a fake Redmine.

## License

MIT, see LICENSE. The label font is DejaVu Sans Bold, cut down to Latin letters (see `fonts/LICENSE`).
