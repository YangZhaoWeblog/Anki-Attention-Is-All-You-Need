# Attention Is All You Need

An Anki attention-budget dashboard. It uses Anki's FSRS simulator and review history to answer three questions:

- How much review attention will the current collection consume?
- How many new cards can the chosen daily budget support?
- Which cards consume the most attention and need review?

The dashboard runs locally. The add-on starts an HTTP server on `127.0.0.1` and opens the full interface in the system browser.

## Install For Development

```bash
ln -sfn "$PWD/addon" "$HOME/Library/Application Support/Anki2/addons21/attention_is_all_you_need"
```

Restart Anki, then click `Attention` in the top toolbar or open `Tools -> Attention Is All You Need`.

## Configuration

Open the add-on configuration in Anki:

- `managedDecks`: root decks included in the global attention budget.
- `dailyBudgetMinutes`: default daily budget; the dashboard can override it.
- `simulationDays`: FSRS simulation horizon.
- `dashboardPort`: preferred local server port.

## Test

```bash
python3 -m unittest discover -s tests -v
```

## Package

```bash
./scripts/package.sh
```

The package is written to `dist/Attention-Is-All-You-Need.ankiaddon`.

## License

GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
