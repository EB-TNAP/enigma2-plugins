# Blindscan Refactor Findings

Bugs/observations noted during the split. Each item is a separate fix after the refactor is complete.

## Pre-existing pyflakes warnings (baseline — do not count as regressions)

- `plugin.py:929` — local `from Screens.MessageBox import MessageBox` inside `exit_plugin()` is unused (the module-level import is used)
- `plugin.py:2900` — local variable `transponder` assigned but never used in `startDishMovingIfRotorSat()`
- `plugin.py:2913` — local `import time` inside `getSignalLock()` is unused

## Findings (post-refactor fixes)

_none yet_
