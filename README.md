# Attention Is All You Need

An Anki attention-budget dashboard. It uses Anki's FSRS simulator and review history to answer three questions:

- How much review attention will the current collection consume?
- How many new cards can the chosen daily budget support?
- Which cards consume the most attention and need review?

The Prism White dashboard supports the complete loop inside Anki: inspect real
rendered cards, edit original fields and tags, suspend or resume a card, mark
a note for optimization, hide a note from candidates, and delete a note after
reviewing the full affected-card scope.

## Architecture

The dashboard renders in an Anki **WebView window** (no external browser, no local
HTTP server). JavaScript calls into Python via the Anki `pycmd` bridge; Python builds
the dashboard in a background **`QueryOp`** (read-only collection access off the main
thread) and pushes the result back to JS, which only renders it.

```
attention_dashboard.py   pure business logic (no aqt/anki; fully unit-tested)
data.py                 deep data module: AttentionData interface + AnkiData
fsrs_adapter.py         ONLY place that touches the private FSRS protobuf backend
dashboard_window.py      Qt dialog + AnkiWebView + QueryOp + JS bridge
__init__.py             menu/toolbar entry; opens the window
```

The FSRS simulator uses Anki's private `_backend.simulate_fsrs_review`. That call is
isolated to `fsrs_adapter.py` behind a version gate: if the method is missing or the
generated protobuf moves, it degrades to a schedule-estimate instead of crashing.

## Requirements

- Anki with the FSRS scheduler, point version ≥ 240400 (Anki 24.04, FSRS as
  the default scheduler). `manifest.json` declares `min_point_version: 240400`
  and `max_point_version: 0` (forward compatible). Older Anki won't load the
  add-on — the FSRS simulator / easy-days fields it depends on aren't there.
  The `fsrs_adapter` still degrades at runtime if the private backend drifts
  on a supported version.

## Install For Development

```bash
ln -sfn "$PWD/addon" "$HOME/Library/Application Support/Anki2/addons21/attention_is_all_you_need"
```

Restart Anki, then click `Attention` in the top toolbar or open `Tools -> Attention Is All You Need`.

## Configuration

Open the add-on configuration in Anki:

- **settingsByProfile**: the last confirmed deck ID and daily budget for each
  Anki profile. A deck rename follows its ID; a deleted deck requires a new
  selection and never widens the scope automatically.
- **dailyBudgetMinutes**: first-run default daily budget.
- **managedDecks**: retained for compatibility with the previous dashboard;
  it does not override a profile's selected deck.
- `simulationDays`: FSRS simulation horizon.

## Test

```bash
python3 -m unittest discover -s tests -v
```

To run the real collection and offscreen WebView smoke tests against the
installed Anki packages:

~~~bash
ANKI_PY=/path/to/python3.13
ANKI_PACKAGES=/Applications/Anki.app/Contents/Resources/app_packages
PYTHONPATH="$ANKI_PACKAGES" "$ANKI_PY" -m unittest tests.test_anki_integration -v
PYTHONPATH="$ANKI_PACKAGES" QT_QPA_PLATFORM=offscreen "$ANKI_PY" scripts/anki_gui_smoke.py
PYTHONPATH="$ANKI_PACKAGES" QT_QPA_PLATFORM=offscreen "$ANKI_PY" scripts/anki_gui_regressions.py
PYTHONPATH="$ANKI_PACKAGES" QT_QPA_PLATFORM=offscreen "$ANKI_PY" scripts/anki_gui_queue_regression.py
~~~

The minimum supported version remains Anki 24.04. The implementation has been
runtime-verified on Anki 26.08.1; release review still needs a 24.04
compatibility run.

## Package

```bash
./scripts/package.sh
```

The package is written to `dist/Attention-Is-All-You-Need.ankiaddon`.

## License

GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
