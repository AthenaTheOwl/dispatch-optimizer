# Compare dispatch policies on the same realized world

Method update: October 18, 2026. Proposed experiments below are not reported as executed results.

The simulator already evaluates routes with a common constraint engine. Preserve that advantage when comparing greedy dispatch with Hungarian batches: both policies should receive identical order arrivals, capabilities, shift windows and realized travel shocks for each seed. Different worlds can make a policy difference look larger or smaller than it is. Travel times are deterministic today (the `sampled` travel-time mode falls back to expected times), so any shock added later must be drawn once per seed and shared.

`python -m experiments.constraint` already separates faster order entry from service capacity: `--front-speedup 2.0` admits work twice as fast while driver shifts stay fixed, and `--policy` selects greedy or Hungarian. A useful next step runs both policies on the same seed and reports queue growth, deadline misses and served orders together. A shorter assignment time can coexist with worse delivery performance. Orders left pending at the simulation cutoff must remain visible in the denominator.

The next proposed fixture uses a cold-chain order and a near-end-of-shift driver, then applies a shared pickup delay. Compare completion, infeasibility, waiting minutes and cost per served order across policies. Reject a result that improves mean delivery time merely by serving fewer difficult orders. Archive the seed, horizon, scenario and policy versions so a route-evaluator change triggers the relevant paired rerun.
