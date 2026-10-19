# Compare dispatch policies on the same realized world

Method update: October 18, 2026. Proposed experiments below are not reported as executed results.

The simulator already evaluates routes with a common constraint engine. Preserve that advantage when comparing greedy dispatch with Hungarian batches: both policies should receive identical order arrivals, capabilities, shift windows and realized travel shocks for each seed. Different worlds can make a policy difference look larger or smaller than it is.

A useful next experiment separates faster order entry from service capacity. If the front end admits work twice as fast while driver shifts stay fixed, report queue growth, deadline misses and served orders together. A shorter assignment time can coexist with worse delivery performance. Orders left pending at the simulation cutoff must remain visible in the denominator.

The next proposed fixture uses a cold-chain order and a near-end-of-shift driver, then applies a shared pickup delay. Compare completion, infeasibility, waiting minutes and cost per served order across policies. Reject a result that improves mean delivery time merely by serving fewer difficult orders. Archive the seed, horizon, scenario and policy versions so a route-evaluator change triggers the relevant paired rerun.

## References

- https://github.com/3orionkings/ai-product-team/blob/main/workflows/process-change.md
