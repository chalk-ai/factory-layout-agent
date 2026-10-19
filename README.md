# Factory layout agent

Ask for a production line ("Set up a manufacturing line for a chalkboard.") and Claude designs
it: process steps, machine counts sized to the target rate, and a position for every station on a
floor plan. Each proposal goes to a constraint solver ([OR-Tools CP-SAT](https://developers.google.com/optimization/cp/cp_solver))
that checks it against the building's rules and returns exactly what is wrong. Claude fixes the
layout and checks again; the loop only finishes on a layout the solver accepts.

The whole thing runs as one [Chalk](https://chalk.ai) scaling group, and Claude is reached through
the Chalk model gateway. Live demo: <https://chalk.ai/demos/factory-layout-agent>.

| File | What it is |
| --- | --- |
| `factory.py` | The building (72 x 40 m, aisle, columns, exits, docks, exhaust wall), the equipment catalog and the presets |
| `solver.py` | `verify`: the CP-SAT check with one assumption literal per rule; `repair`: the nearest valid placement |
| `agent.py` | The Claude tool loop: `get_site`, `list_equipment`, `check_layout` |
| `server.py` | FastAPI: `/api/design` (NDJSON stream), `/api/verify`, `/api/site`, `/api/sample` |
| `index.html`, `app.js` | The page: floor plan, solver verdict and agent feed (`?embed=1` for iframes) |
| `sample_run.json` | A recorded run the page replays on load |
| `deploy.py` | Deploys all of the above as a `ScalingGroup` |
| `example.py` | A client for the API |

## Run it

```bash
chalk login                   # or export CHALK_CLIENT_ID / CHALK_CLIENT_SECRET
chalk router status           # the environment needs the model gateway enabled
chalk router api-key create   # store the key as the Chalk secret CHALK_ROUTER_API_KEY
./deploy.py                   # prints web_url
./example.py https://<web_url> "Set up a manufacturing line for a chalkboard."
```

`deploy.py` takes `--model` (default `anthropic/claude-opus-5-5`), `--effort` (default `medium`;
the hosted demo runs `low`), `--env-id` and `--key-secret`.

To check a layout you wrote yourself, without the model:

```bash
./example.py https://<web_url> --verify my_layout.json
```

## How the check works

Every rule is an instance with its own CP-SAT assumption literal: inside the building, access
clearance for every pair of machines, clear of columns, clear of the aisle and exit zones, hazard
separation (spray vs. dust, sparks and hot surfaces), exhaust equipment on the north wall,
receiving and shipping at their docks, material-flow distance between consecutive steps, and
capacity per step against the target rate.

`verify` pins every station at the agent's coordinates and solves under all the assumptions.
While the model is infeasible, CP-SAT returns a set of assumptions that cannot all hold; those
rules are recorded as violated and dropped, and the model is solved again. When it becomes
feasible, the remaining rules are proven to hold together. Each violation is then explained with
the stations and distances involved, computed from the same geometry.

When the only problems are geometric, `repair` frees the coordinates, makes every rule hard and
minimizes total movement, so the agent also gets the smallest set of moves that would fix the
layout. Capacity problems are left to the agent: the solver does not add machines.

## API

`POST /api/design` with `{"prompt": "..."}` streams one JSON object per line:

| `type` | Fields |
| --- | --- |
| `start` | `model`, `effort` |
| `thinking_delta`, `text_delta` | `text` (summarized reasoning, then the reply) |
| `tool` | `name`, `summary` (`get_site`, `list_equipment`) |
| `writing` | `round`, `chars`: the layout being written |
| `verdict` | `round`, `valid`, `violations`, `families`, `constraints`, `solve_ms`, `layout`, `repair` |
| `done` | `valid`, `summary`, `checks`, `elapsed_s`, `tokens` |
| `error` | `message` |

`POST /api/verify` takes a layout and returns the same verdict fields. A layout looks like:

```json
{
  "product": "chalkboard",
  "rate_per_hour": 60,
  "steps": [{"id": "recv", "name": "Receive", "after": []},
            {"id": "cut", "name": "Cut panels", "after": ["recv"]}],
  "stations": [{"id": "recv1", "type": "receiving_bay", "step": "recv", "x": 0, "y": 23},
               {"id": "saw1", "type": "panel_saw", "step": "cut", "x": 0.5, "y": 30.7, "rotated": false}]
}
```

## Measured

On the hosted demo (Claude Opus 5.5 at `effort: low` through the Chalk model gateway, 2 CPUs,
4 GiB), October 2026:

| Prompt | Solver checks | Violations per round | Stations | Rules checked | Verify time per check | Run time |
| --- | --- | --- | --- | --- | --- | --- |
| Chalkboards (default prompt) | 1 | 0 | 16 | 206 | 29 ms | 53 s |
| Chalkboards (default prompt), another run | 1 | 0 | 16 | 206 | 30 ms | 51 s |
| Chalkboards, 90/h, separate primer and coat | 1 | 0 | 28 | 556 | 70 ms | 94 s |
| Chalk sticks, 3,000/h | 2 | 1, then 0 | 12 | 131 | 32 ms, 20 ms | 63 s |
| Erasers, 300/h | 2 | 2, then 0 | 15 | 174 | 49 ms, 27 ms | 107 s |
| Erasers, 300/h, run from the page | 2 | 6, then 0 | 15 | 174 | 90 ms, 28 ms | 101 s |

Each run used 15k-33k input and 5k-10k output tokens. Every first-round violation broke a spacing or a
flow-distance rule, and every one was fixed on the next check.

A cold deploy took 60 s; redeploying changed app files took a few seconds.

## Notes

- The building, catalog and rules are fictional and simplified. They are not engineering or
  fire-code advice.
- Hazard separation is checked as a gap along at least one axis, which is stricter than straight-line
  distance.
- The hosted demo is public and unauthenticated. The server caps live runs at 2 at a time and 40
  an hour across the replica, prompts at 300 characters and layouts at 64 KiB, because client IPs
  are not visible behind the gateway.
- `sample_run.json` is a real recorded run; the page compresses its waiting time when replaying.
- Delete the deployment with `ScalingGroup.from_name("factory-layout-agent").delete()` using the
  same `CHALK_*` environment variables.

## License

Apache-2.0. See `LICENSE`.

## Local authentication

The SDK reads credentials, API host, and the selected environment from your existing
`chalk login` in `~/.chalk.yml`, including project-scoped and default logins.
For unattended runs, `CHALK_CLIENT_ID` and `CHALK_CLIENT_SECRET` remain supported. Where available, `--env-id`
overrides the target environment; otherwise the SDK uses your login or environment settings.
