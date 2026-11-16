# AC Infinity integration (vendored)

Copied unchanged from https://github.com/hunterjm/ac-infinity-hacs, release
**v2.0.0** (2026-09-19), MIT licence (see LICENSE). Kept in git instead of
installed through HACS, so the Pi needs no GitHub login and the version only
changes on purpose.

Home Assistant installs its library (`ac-infinity-ble==1.0.0`) from PyPI the
first time the integration loads.

To upgrade: download the new release, replace this folder's files with its
`custom_components/ac_infinity/`, update the version here, check the release
notes for the Home Assistant version it needs, restart Home Assistant.
