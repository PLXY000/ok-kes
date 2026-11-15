"""Persistent, UI-independent queue for character farming sessions.

This module deliberately consumes explicit observations. It does not perform
OCR, click controls, or switch the active game character.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class FarmMode(str, Enum):
    ZERO_SYSTEM = "zero_system"
    SEASON = "season"


class QueueStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED_UNCERTAIN = "paused_uncertain"
    GRADUATED = "graduated"


@dataclass(frozen=True)
class GraduationCriteria:
    core_cards: bool = False
    target_shine: bool = False
    finished_equipment: bool = False


@dataclass(frozen=True)
class CharacterTargets:
    """Per-character target names read from a profile's archive page."""
    core_cards: list[str] = field(default_factory=list)
    target_shines: list[str] = field(default_factory=list)
    finished_equipment: list[str] = field(default_factory=list)


@dataclass
class ArchiveSnapshot:
    character_id: str
    complete: bool
    cards: list[str] = field(default_factory=list)
    flashing_cards: list[str] = field(default_factory=list)
    equipment: list[str] = field(default_factory=list)


def is_graduated(criteria: GraduationCriteria, targets: GraduationCriteria | None = None) -> bool:
    """A character graduates when any two enabled goals have been met."""
    enabled = targets or GraduationCriteria(True, True, True)
    return sum((
        enabled.core_cards and criteria.core_cards,
        enabled.target_shine and criteria.target_shine,
        enabled.finished_equipment and criteria.finished_equipment,
    )) >= 2


def is_archive_profile_page(ocr_texts: list[str]) -> bool:
    """Fail-closed page check using both title and selected section labels.

    It only confirms the screen type. It does not identify the character or
    interpret archive contents.
    """
    normalized = "".join("".join(text.split()) for text in ocr_texts)
    has_detail_title = any(marker in normalized for marker in ("详细资讯", "詳細資訊"))
    has_archive_section = any(marker in normalized for marker in ("存档资料", "存檔資料"))
    return has_detail_title and has_archive_section


def evaluate_archive_snapshot(
    snapshot: ArchiveSnapshot,
    expected_character_id: str,
    targets: CharacterTargets,
) -> GraduationCriteria | None:
    """Evaluate a complete, identity-matched archive snapshot; None means pause."""
    if not snapshot.complete or not expected_character_id or snapshot.character_id != expected_character_id:
        return None
    configured = (targets.core_cards, targets.target_shines, targets.finished_equipment)
    if sum(bool(values) for values in configured) < 2:
        return None

    def has_all(expected: list[str], observed: list[str]) -> bool:
        if not expected:
            return False
        normalized_observed = {_normalize_game_text(value) for value in observed}
        return all(_normalize_game_text(value) in normalized_observed for value in expected)

    return GraduationCriteria(
        core_cards=has_all(targets.core_cards, snapshot.cards),
        target_shine=has_all(targets.target_shines, snapshot.flashing_cards),
        finished_equipment=has_all(targets.finished_equipment, snapshot.equipment),
    )


def _normalize_game_text(value: str) -> str:
    return "".join(value.split()).casefold()


@dataclass(frozen=True)
class CharacterProfile:
    character_id: str
    display_name: str
    enabled: bool = True
    # Kept per mode so zero-system and season goals can differ independently.
    zero_system: GraduationCriteria = field(default_factory=GraduationCriteria)
    season: GraduationCriteria = field(default_factory=GraduationCriteria)
    zero_system_targets: CharacterTargets = field(default_factory=CharacterTargets)
    season_targets: CharacterTargets = field(default_factory=CharacterTargets)

    def goals_for(self, mode: FarmMode) -> GraduationCriteria:
        return self.zero_system if mode is FarmMode.ZERO_SYSTEM else self.season

    def targets_for(self, mode: FarmMode) -> CharacterTargets:
        return self.zero_system_targets if mode is FarmMode.ZERO_SYSTEM else self.season_targets


@dataclass(frozen=True)
class RunObservation:
    core_cards: bool
    target_shine: bool
    finished_equipment: bool

    @property
    def criteria(self) -> GraduationCriteria:
        return GraduationCriteria(self.core_cards, self.target_shine, self.finished_equipment)


@dataclass
class CharacterRun:
    status: RunStatus = RunStatus.PENDING
    completed_runs: int = 0
    last_observation: dict[str, bool] | None = None


@dataclass
class SessionState:
    mode: FarmMode
    profiles: list[CharacterProfile]
    runs: dict[str, CharacterRun] = field(default_factory=dict)
    current_character_id: str | None = None
    queue_status: QueueStatus = QueueStatus.READY


class FarmQueue:
    """Small state machine; callers persist state after each transition."""

    def __init__(self, profiles: list[CharacterProfile], mode: FarmMode, state: SessionState | None = None):
        ids = [profile.character_id for profile in profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("character_id values must be unique")
        self.state = state or SessionState(mode=mode, profiles=profiles)
        if self.state.mode is not mode:
            raise ValueError("mode does not match saved session state")
        if not self.state.runs:
            self.state.runs = {profile.character_id: CharacterRun() for profile in profiles if profile.enabled}
        self._sync_current()

    @property
    def current_profile(self) -> CharacterProfile | None:
        return next((p for p in self.state.profiles if p.character_id == self.state.current_character_id), None)

    def start_current(self) -> None:
        if self.state.queue_status in (QueueStatus.PAUSED, QueueStatus.COMPLETED):
            raise RuntimeError(f"cannot start queue in {self.state.queue_status.value} state")
        self._sync_current()
        if self.state.current_character_id is None:
            self.state.queue_status = QueueStatus.COMPLETED
            return
        run = self.state.runs[self.state.current_character_id]
        run.status = RunStatus.RUNNING
        self.state.queue_status = QueueStatus.RUNNING

    def record_observation(self, observation: RunObservation | None) -> None:
        if self.state.queue_status is not QueueStatus.RUNNING or self.state.current_character_id is None:
            raise RuntimeError("no character run is active")
        run = self.state.runs[self.state.current_character_id]
        if observation is None:
            run.status = RunStatus.PAUSED_UNCERTAIN
            self.state.queue_status = QueueStatus.PAUSED
            return
        profile = self.current_profile
        assert profile is not None
        run.completed_runs += 1
        run.last_observation = asdict(observation.criteria)
        if is_graduated(observation.criteria, profile.goals_for(self.state.mode)):
            run.status = RunStatus.GRADUATED
            self._sync_current()
        else:
            run.status = RunStatus.PENDING
            self.state.queue_status = QueueStatus.READY

    def resume(self) -> None:
        if self.state.queue_status is not QueueStatus.PAUSED:
            raise RuntimeError("only a paused queue can be resumed")
        current = self.state.runs.get(self.state.current_character_id or "")
        if current and current.status is RunStatus.PAUSED_UNCERTAIN:
            current.status = RunStatus.PENDING
        self.state.queue_status = QueueStatus.READY

    def _sync_current(self) -> None:
        for profile in self.state.profiles:
            run = self.state.runs.get(profile.character_id)
            if profile.enabled and run and run.status is not RunStatus.GRADUATED:
                self.state.current_character_id = profile.character_id
                if self.state.queue_status is not QueueStatus.PAUSED:
                    self.state.queue_status = QueueStatus.READY
                return
        self.state.current_character_id = None
        self.state.queue_status = QueueStatus.COMPLETED


def save_state(path: str | Path, state: SessionState) -> None:
    """Atomically save a resumable checkpoint beside the target file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _state_to_dict(state)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def load_state(path: str | Path) -> SessionState:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return _state_from_dict(raw)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid farm checkpoint: {path}") from exc


def _state_to_dict(state: SessionState) -> dict[str, Any]:
    return {
        "version": 1,
        "mode": state.mode.value,
        "profiles": [asdict(profile) for profile in state.profiles],
        "runs": {key: {"status": value.status.value, "completed_runs": value.completed_runs,
                        "last_observation": value.last_observation} for key, value in state.runs.items()},
        "current_character_id": state.current_character_id,
        "queue_status": state.queue_status.value,
    }


def _state_from_dict(raw: dict[str, Any]) -> SessionState:
    if raw.get("version") != 1:
        raise ValueError("unsupported checkpoint version")
    profiles = []
    for item in raw["profiles"]:
        profiles.append(CharacterProfile(
            character_id=item["character_id"], display_name=item["display_name"], enabled=item.get("enabled", True),
            zero_system=GraduationCriteria(**item.get("zero_system", {})),
            season=GraduationCriteria(**item.get("season", {})),
            zero_system_targets=CharacterTargets(**item.get("zero_system_targets", {})),
            season_targets=CharacterTargets(**item.get("season_targets", {})),
        ))
    runs = {key: CharacterRun(RunStatus(value["status"]), int(value.get("completed_runs", 0)),
                              value.get("last_observation")) for key, value in raw["runs"].items()}
    return SessionState(FarmMode(raw["mode"]), profiles, runs,
                        raw.get("current_character_id"), QueueStatus(raw["queue_status"]))



