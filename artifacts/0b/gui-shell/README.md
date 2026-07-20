# 0B GUI shell evidence artifacts

Generated on 2026-07-20 for branch `spike/0b-repository-contracts-preflight`.

- `gui-shell-light-100.png`, `gui-shell-light-150.png`, and
  `gui-shell-light-200.png` are native Windows Qt renders of the minimal
  light-theme shell at 100, 150, and 200 percent scale with the Norwegian
  flag-selected shell labels.
- `gui-shell-dark-100.png`, `gui-shell-dark-150.png`, and
  `gui-shell-dark-200.png` are the matching dark-theme renders.
- Each PNG has a neighboring JSON manifest recording pixel dimensions,
  sampled-color count, and non-background sample ratio from
  `tools/render_gui_shell.py`.

The evidence is intentionally limited to the 0B shell frame: navigation,
action bar, workspace, activity bar, theme tokens/QSS, icon registry, Engine
Host status display, the non-mutating standard backup setup surface,
flag-based language selector label switching, and the read-only backup job
detail panel plus activity-bar plan preview. It does not claim completion of
real job creation, the full GUI workflow, full localization matrix,
accessibility review, or final visual acceptance suite.

The render command uses the native Windows Qt platform by default on Windows.
Qt's `offscreen` platform is still used by automated smoke tests, but this
local environment exposes no font families through `offscreen`, which turns
text into placeholder glyphs and is unsuitable for visual reference images.
