# Price window: fast export, hold and charge decisions during price volatility

Date: 2026-10-12
Status: draft for review

## Problem

On 2026-10-12 between 15:15 and 16:35 AEDT the feed-in price spiked to
$24.23/kWh twice, sat at $10.48-15.73 for half an hour, then alternated between
about $10.4 and $0.64 in adjacent 5-minute intervals. The existing combination
of the EMHASS plan (re-solved every 2 minutes, 10-minute slots) and a
price-triggered `force_export` automation coped, but not well:

1. **Export never reached the 10 kW limit in any spike interval.** Averages
   were 6.4-8.5 kW. Holding 10 kW across the eleven spike intervals would have
   earned roughly $30 more on about $93 (estimated from 5-minute means).
   - `force_export` commands only the Voltx. The Solax is released to
     self-consumption (`coordinator.py`, `_async_handle_override`), so its 3 kW
     was idle at $10-24.
   - Each on/off spike started 18-38 s late (Amber Express confirmation delay)
     and then ramped from the EMHASS level.
2. **The EMHASS loop cannot follow 5-minute prices.** A manual change of the
   plan export limit to 10 kW at 16:34:44 took effect as the spike ended, and
   the plan then targeted 10 kW of export at 17c.
3. **The energy question was answered by hand.** The user lowered the plan
   export limit, selected `self_consume` at 66c-$1.04, and ran `force_charge`
   four times at 26-44c, to hold energy for further spikes. On the day energy
   was never the constraint (SOC went 96% -> 76% over the event), so holding
   cost a little and gained nothing. But on earlier occasions a simple
   "full blast" export has emptied the battery while the spike continued, and
   power was bought back at spike prices.

## Goal

During a period of forecast price spikes, the coordinator decides every tick
whether to export at full power, hold, or charge at full power, using both
batteries, such that:

- energy is sold at good prices whenever the battery can afford it;
- when energy is tight it is kept for the highest-priced intervals;
- enough energy is always kept to run the house through the risk period, so a
  spike that continues never forces a buy-back;
- every decision carries a reason that can be read live and audited afterwards.

Outside such a period nothing changes: EMHASS tracking runs as today.

## Non-goals

- Changing the EMHASS runner, its slot length or its cadence.
- Tuning `emhass_plan_export_limit` dynamically. It returns to a fixed setting.
- Price sources other than Amber Express.
- Predicting spikes Amber Express does not forecast.

## Data source

Amber Express is the source of all price data. The coordinator reads the
`detailedForecast` attribute of two configured entities:

- feed-in channel (live: `sensor.amber_express_home_feed_in_price`)
- general channel (live: `sensor.amber_express_home_general_price`)

Observed shape on 2026-10-12: about 40 minutes of 5-minute intervals followed
by 30-minute intervals out to roughly 24 hours. Each entry has `start_time`,
`end_time`, `duration`, `per_kwh`, `estimate`, `spike_status`, `descriptor`,
and (for estimates) `advanced_price_predicted` with `low`, `predicted` and
`high`. The first entry is the current interval; `estimate: false` marks a
confirmed price.

Amber's `descriptor` is not used. Live values are
`extremelyLow / veryLow / low / neutral / high / spike` and are relative, not
fixed price bands: feed-in read `high` at 17c during the event.

## Design

### Module `price_window.py`

New module with no Home Assistant state access. Two public functions.

**`parse_amber_express(feed_in_attrs, general_attrs, now) -> list[PriceInterval]`**

The only code that knows Amber Express field names. Joins the two channels on
interval start. Each `PriceInterval` has:

| Field | Source |
|---|---|
| `start`, `end` | `start_time`, `end_time` |
| `sell` | feed-in `per_kwh` |
| `sell_high` | feed-in `advanced_price_predicted.high`, else `per_kwh` |
| `buy` | general `per_kwh` |
| `buy_high` | general `advanced_price_predicted.high`, else `per_kwh` |
| `confirmed` | `estimate` is false on both channels |
| `flagged` | `spike_status` is `potential` or `spike` on either channel |

Intervals that have ended are dropped. Entries with unparseable times or
prices are skipped. Returns an empty list if no interval covers `now`.

Amber Express writes each start one second late (`02:00:01`) and each end on
the boundary (`02:05:00`). Starts are rounded down to the minute, so the first
second of an interval is covered.

**`decide_price_window(intervals, now, energy, limits, settings) -> PriceWindowDecision`**

Pure. Steps:

1. **Find the window.** An interval *qualifies* if `sell_high >= spike
   threshold` or it is `flagged`. Only qualifying intervals that start within
   the lookahead are considered. If there are none, the window is inactive.
   The window runs from `now` to the end of the last such interval.
2. **House reserve.** Reserve horizon = window end + reserve margin, or, if a
   reserve-until clock time is set, the next occurrence of that time (today if
   still ahead, otherwise tomorrow). House reserve = expected house load x time
   from `now` to the horizon, divided by discharge efficiency (the square root
   of the round-trip efficiency). Solar is not counted, which errs on the side
   of keeping more.
3. **Surplus.** Usable energy above the SOC floor (Voltx plus Solax, when the
   Solax is under control) minus the house reserve, floored at zero.
4. **Allocate.** Take every interval from `now` to the window end whose
   `sell_high` is at or above the minimum sell price. Ranking price is `sell`
   for a confirmed current interval and `sell_high` otherwise, with one
   exception (spike in hand, below). Sort highest first. Give each its full
   export energy (maximum export power x remaining duration, divided by
   discharge efficiency) until the surplus is spent. The last interval served
   may be partly served.

   **Spike in hand.** If the current interval is confirmed at or above the
   spike threshold, later intervals are ranked on `sell` (their predicted
   price) instead of `sell_high`. A real spike price is then held back only
   for an interval predicted to beat it, never for one that merely could.
   Without this, a confirmed $10.48 would be held for intervals predicted at
   60c whose range tops out at $12.
5. **Decide.**
   - `export` at maximum export power if the current interval received any
     allocation.
   - `charge` at maximum charge power if at least one qualifying interval was
     left unserved, the batteries are below the SOC ceiling, `buy` is at or
     below the maximum charge buy price (when that setting is set), and
     `buy / round-trip efficiency * charge ratio <= predicted sell` of the
     cheapest unserved qualifying interval. Charging uses the `predicted`
     price, not `sell_high`. The maximum charge buy price is an absolute cap
     that overrides the formula; if it is unset the formula alone decides.
     While the current interval is unconfirmed, `buy` here is `buy_high`:
     charging does not start on a price that could still turn out to be a
     spike.
   - `hold` otherwise.

**Interval boundaries.** When the clock passes into a new interval before
Amber Express has confirmed its price, that interval is still an estimate and
is ranked on `sell_high`. If it might spike, export continues across the
boundary without waiting for confirmation.

**Why two sell prices.** Amber Express gives each forecast interval a
`predicted` price and a `low` to `high` range. `sell` is the single price: the
actual price once the interval is confirmed, the prediction before that.
`sell_high` is the top of the range. A confirmed current interval is ranked on
`sell` because its price is known. Every other interval is ranked on
`sell_high`, so an interval that might spike is treated as if it will, and
energy is kept for it. This is deliberately one-sided. Keeping energy for a
spike that does not arrive costs little: the energy is still in the battery.
Selling it cheaply just before a spike that does arrive is the expensive
mistake. Charging is the opposite case, since buying costs money now, so the
charge rule uses `predicted`.

**No power moderation.** Export is either full power or none. Revenue in an
interval is price x energy, so with a fixed amount of energy the best plan
fills the highest-priced intervals to the export limit before any goes to a
lower-priced one. Selling at reduced power in a lower-priced interval to keep
some back is a weaker version of the same choice the allocation already makes:
if the energy is needed for higher-priced intervals, the lower one gets none;
if it is not, the lower one gets full power. Only the last interval served can
be partly served, and it exports at full power until its share is spent.

### Coordinator integration

Tick order in `_async_update_data`:

1. Disabled gate (unchanged).
2. Manual override (unchanged). The override select and `set_mode` still win.
3. **Price window (new).**
4. EMHASS tracking (unchanged).

When the price window is enabled, has fresh data and is active, its decision
replaces the EMHASS grid target for the tick and the existing tracking path
executes it:

| Decision | Grid target |
|---|---|
| `export` | minus the export limit |
| `charge` | plus the charge power, bounded by the import limit |
| `hold` | zero (the existing self-consumption handoff) |

The battery setpoint that goes with the target is anchored on the grid power
with the batteries' own output added back, so the target is reached in one
step. For that reason the proportional grid correction (tier 2), transient
damping and the entity-driven grid-priority mode are switched off on a price
window tick, and the self-consumption handoff's minimum dwell does not delay a
change of decision.

**Hold while the EV charges.** The existing rule that the EV is served from
the grid still applies, so `hold` does not use the handoff then. The batteries
carry the rest of the house and the grid target is the EV's own draw. Without
this the whole house would be bought from the grid for as long as the car
charged.

No new inverter write paths are added. The Voltx/Solax split, import and
export ceilings, EV headroom, monitored-load headroom and SOC limits all apply
as they do in tracking. The plan-staleness check does not apply to a price
window tick, because the plan is not being followed.

**Ramp.** `DEFAULT_RAMP_STEP` is 1500 W per tick, so going from 5 kW to 9.5 kW
takes about 30 s. The price window bypasses the ramp step: on the first tick
after a change of decision (to `export`, `charge` or `hold`) the new target is
commanded directly. The implementation plan confirms this against the Voltx's
actual response.

**Stale data.** If either Amber Express entity is unavailable, its
`detailedForecast` is missing, no interval covers `now`, or the entity was
last updated more than a fixed age ago (10 minutes), or the expected house
load entity is unreadable, the price window stands down for the tick and
EMHASS tracking runs.

**Shadow mode.** When the shadow setting is on, the decision is computed and
reported every tick but never replaces the grid target. Shadow is on unless
the shadow entity reads exactly `off`: unset, missing or unavailable all mean
shadow.

### Reporting

New `CoordinatorMode` values: `price_window_export`, `price_window_hold`,
`price_window_charge`. A binding constraint (export ceiling, SOC floor and so
on) is still reported by the mode sensor as today. The reason says why the
decision was made; the mode says what bound it.

New sensors:

| Sensor | State |
|---|---|
| Price window reason | reason code (below) |
| Price window end | timestamp, unknown when inactive |
| Price window house reserve | kWh |
| Price window surplus | kWh |

### The decision reason

The reason is produced by `decide_price_window` itself, from the same values
that made the choice, and returned as part of `PriceWindowDecision`. It has
three layers.

**1. Reason code** (state of the reason sensor):

| Code | Meaning |
|---|---|
| `disabled` | Price window is not enabled |
| `no_window` | No qualifying interval within the lookahead |
| `data_stale` | Amber Express data missing or old |
| `export_allocated` | Current interval is among those the surplus can serve |
| `export_unconfirmed` | Current interval not yet confirmed; served on its high bound |
| `hold_below_min_price` | Current price is under the minimum sell price |
| `hold_outranked` | Surplus is reserved for higher-priced intervals |
| `hold_no_surplus` | The house reserve takes all usable energy |
| `hold_charge_too_dear` | Shortfall exists, but the buy price fails the charge rule or exceeds the maximum charge buy price |
| `hold_soc_ceiling` | Shortfall exists and the charge rule passes, but the batteries are full |
| `charge_shortfall` | Qualifying intervals unserved and the charge rule passes |

Hold codes are checked in this order: `hold_no_surplus`,
`hold_below_min_price`, `hold_outranked`; then, only if a qualifying interval
is unserved, `hold_soc_ceiling` or `hold_charge_too_dear` replaces it. The
sentence then keeps the first reason and adds why it is not charging.

**2. Sentence** (attribute `reason`), with the figures, for example:

- "Exporting: $10.48 now ranks 3rd of 9 sellable intervals; surplus 31.2 kWh
  covers all 9 (needs 6.4 kWh)."
- "Holding: 64c now, but the 2.1 kWh surplus is reserved for 3 intervals at
  $10.48 or more, through 16:35."
- "Charging: buying at 28c; 4 spike intervals unserved, cheapest predicted
  $9.80; shortfall 3.3 kWh."

**3. Working** (attributes):

- `window_start`, `window_end`, `reserve_horizon`, `reserve_basis`
  (`forecast_margin` or `clock_time`)
- `usable_kwh`, `house_reserve_kwh`, `surplus_kwh`, `shortfall_kwh`
- `current_sell`, `current_buy`, `current_confirmed`, `current_rank`
- `intervals`: one row per interval in the window with start, ranking price,
  basis (`confirmed`, `high` or `predicted`), `qualifies`, and allocated kWh
- `shadow` and, in shadow mode, `would_command_w`

The `intervals` attribute is limited to the window, so it stays small, and it
is recorded with the sensor. This preserves the forecast Amber Express showed
at each decision, which was not available when reviewing 2026-10-12.

Each change of reason code is logged once at info level with the sentence.

Codes and sentences are identical in shadow and live modes.

### Settings

All in the options flow, in a new fourth step, "Price Window". A setting left
blank falls back to its default. Every numeric or time setting accepts either a
literal or a helper entity ID, read with the existing `_float_or_entity`
pattern (a time setting accepts `HH:MM` or an `input_datetime`).

| Setting | Default |
|---|---|
| Enable (switch-like entity) | unset, feature off |
| Shadow mode (switch-like entity) | unset, treated as on |
| Amber Express feed-in price entity | unset |
| Amber Express general price entity | unset |
| Spike threshold (feed-in high bound) | 1.00 $/kWh |
| Lookahead | 4 h |
| Reserve margin after last qualifying interval | 2 h |
| Reserve-until clock time | unset |
| Minimum sell price inside a window | 0.30 $/kWh |
| Charge ratio | 1.5 |
| Maximum charge buy price | unset, formula only |
| Round-trip efficiency | 0.85 |
| Expected house load | unset; required to enable |

The feature is inactive unless the enable entity is on and both Amber Express
entities and the house load entity are configured.

## Known trade-off

While a window is active the EMHASS grid target is ignored, including the
quiet hours before a forecast spike. With a 4 h lookahead, a spike forecast
for 18:00 puts the coordinator in `hold` from about 14:00: solar still charges
the battery, but EMHASS cannot make its own import or export choices. The
lookahead setting shortens this if it proves too blunt.

## Testing

`tests/test_price_window.py`:

- **Parser:** mixed 5- and 30-minute intervals; missing
  `advanced_price_predicted`; unparseable entries skipped; channels joined on
  start; no interval covering `now` returns empty.
- **Decision:** plenty of energy (all sellable intervals served); tight energy
  (mid-price current interval outranked); no surplus; shortfall with a cheap
  buy price (charge); shortfall with a dear buy price; buy price above the maximum charge buy
  price; full batteries;
  qualifying interval beyond the lookahead (inactive); clock-time reserve
  today and rolling to tomorrow; unconfirmed current interval served on its
  high bound.
- **Reason:** every code is produced by at least one test, which also asserts
  the figures in the sentence and the allocation rows.
- **Replay of 2026-10-12:** confirmed feed-in and general prices for
  15:00-17:15 as a fixture. The forecasts shown at the time were not recorded,
  so the replay uses reconstructed forecasts. It proves the logic, not
  forecast quality.

Coordinator tests: manual override still wins; stale Amber Express data falls
through to tracking; shadow mode leaves the grid target untouched; the Solax
is commanded during `export`.

## Rollout

1. gridcoordinator: implement on `feat/price-window`, PR, version bump,
   release, update on the HA host.
2. homeassistant-config: create helpers for the settings, point the options at
   them, enable with shadow mode on. The existing "Voltx auto export start",
   "Voltx auto-export stop" and "Price spike export ceiling" automations stay
   in place; they act through the manual override, which takes precedence.
3. After a real event reads correctly in shadow: turn shadow off, then remove
   those three automations and fix `emhass_plan_export_limit` at its normal
   value.

## Changed after the branch review (2026-10-13)

A fresh review of the finished branch found three things that are now fixed, each with a test:

- **Both batteries make up the setpoint.** The combined setpoint is split between Voltx and Solax by the SOC-balance share. If that asks the Solax for more than it can deliver (its 3 kW limit, or its SOC floor or ceiling), the Voltx takes the remainder, and the other way round. Before, the remainder was dropped and export sat 15 to 18% under the limit.
- **A maximum charge buy price that cannot be read blocks charging.** This is the one exception to "an unreadable setting falls back to its default": the default is no cap, and losing the cap silently would allow buying at any price. The reason reads `hold_charge_too_dear` and says the setting cannot be read. A blank setting still means no cap.
- **An interval that will never be sold into is not charged for.** A flagged interval whose top-of-range price is under the minimum sell price no longer counts as unserved.

### Staying inside the grid limits (agreed 2026-10-13)

The review also found that the first design could pass the export or import limit for a tick or more after a change of decision: it re-anchored the setpoint every tick on "grid reading + last commanded battery power", which overstates the load while the batteries or the meter are still catching up. Keeping export at or under 10 kW is the coordinator's overriding purpose, so the control is now:

1. **One direct step.** On the tick the action changes, the setpoint is computed once from the uncontrolled grid power and sent without the ramp. Each battery's actual output is taken to be somewhere between its power reading and its commands of the last 60 s (the Solax has no power reading, so its commands alone). Where that leaves a range, the step uses the end that keeps it inside the limit the action works at: the smallest export, or the smallest import when charging.
2. **Then only the meter.** On later ticks the setpoint moves from the commands actually written, by what the meter reads:
   - over the limit (export limit when exporting, import limit when charging): cut by the full excess at once;
   - short of the target: raised by at most the normal ramp step, less any power already commanded in that direction and not yet seen, and only once the commands have stood for two ticks so the meter can show them.
3. **A command that has stood for 60 s is taken as delivered.** A Voltx that then still reads low is under-delivering, and the shortfall is made up.

The ramp and the tracking deadband are off on every price window tick; the pacing above replaces them.

**What it costs.** After a hold of a minute or more, export is back at the limit on the first tick (within the Solax's small share). After a hold of only seconds, commanded and measured power disagree, the first step is a cautious one and the rest is made up at about 500 W per tick. Making up for an under-delivering Voltx starts after 60 s.

**What it cannot do.** A 10 s loop cannot react inside a tick: a load switching off while exporting at the limit shows as an overshoot until the next tick, as it does today. The inverter's own meter export limit is the backstop for that.

**How it is tested.** A closed-loop simulation runs the real tick against batteries that respond 0 to 40 s late, a meter that lags 0 to 20 s, a Voltx that delivers 500 W less than asked, and export, hold, export sequences with holds of 10 s to 70 s. In every case export stays within 20 W of the limit (whole-watt rounding) and returns to it; the same for the import limit when charging.
