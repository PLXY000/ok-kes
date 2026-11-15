# CZN queue and graduation checkpoint (phase 1)

This phase adds a UI-independent foundation to `ok-kes` for the proposed all-character farming workflow. Archive page confirmation and target evaluation are implemented, but end-to-end OCR extraction, input simulation, and character switching are not connected.

## Scope

- The queue stores ordered, enabled character profiles with independent zero-system and season target settings.
- Each profile tracks three targets: core cards, target shine, and finished equipment.
- Target names can be configured separately for each character and mode. Exact normalized names in a complete, identity-matched archive snapshot are required for each configured category.
- The archive page check requires both the detail title and archive section label, in simplified or traditional Chinese. It confirms only screen type; role identity must be supplied and verified separately.
- A character is graduated when at least two configured targets have been explicitly observed as achieved. Targets not enabled for that profile/mode do not count.
- Queue and per-character run states are checkpointed as versioned JSON. Writes use a temporary file plus atomic replacement so an interrupted write does not leave a partial checkpoint.
- An unknown observation moves the active run and queue to a paused state. Resume keeps the same character at the head of the remaining queue.

## State flow

`ready -> running -> ready` when the character is not graduated; `ready -> running -> next character` when graduated; and `running -> paused` when observation is unavailable. The last completed character moves the queue to `completed`.

## Configuration example

```python
from ok_tasks.czn_farm import CharacterProfile, FarmMode, GraduationCriteria, FarmQueue

profiles = [
    CharacterProfile(
        character_id="character-id",
        display_name="角色名",
        zero_system=GraduationCriteria(core_cards=True, target_shine=True, finished_equipment=True),
        season=GraduationCriteria(core_cards=True, target_shine=False, finished_equipment=True),
    ),
]
queue = FarmQueue(profiles, FarmMode.ZERO_SYSTEM)
```

Persist with `save_state("configs/czn_farm_state.json", queue.state)` after transitions and restore with `load_state(...)`. `evaluate_archive_snapshot(...)` returns `None` when the OCR snapshot is incomplete, has the wrong character ID, or fewer than two target categories are configured. The caller must pause in those cases. The current module does not itself capture OCR regions or visually identify the portrait.

## Safety and limitations

This module does not identify the current in-game character, choose a mode, execute clicks, or switch characters. Those integrations must verify the expected screen and pause whenever OCR is uncertain or the visible UI differs from the expected state. It does not read or modify game memory/files and includes no anti-cheat evasion behavior.

## Tests

Run `python -m unittest tests.TestCznFarm` for the pure state-machine and persistence tests. The repository's existing `run_tests.ps1` also runs the project test files.


