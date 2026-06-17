Run `node scripts/trace_scoring.js "$ARGUMENTS"` and show the full output.

After showing the output:
- If "✓ Paths match exactly" → report it matches, done.
- If "(none stored)" or "✗ MISMATCH" → fix data/sim_cd_paths.json to match the live simulation, then re-run to confirm it now passes. Commit the fix.
