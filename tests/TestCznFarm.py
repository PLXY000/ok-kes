import tempfile
import unittest
from pathlib import Path

from ok_tasks.czn_farm import (
    CharacterProfile,
    CharacterTargets,
    FarmMode,
    FarmQueue,
    ArchiveSnapshot,
    GraduationCriteria,
    QueueStatus,
    RunObservation,
    RunStatus,
    SessionState,
    is_graduated,
    is_archive_profile_page,
    evaluate_archive_snapshot,
    load_state,
    save_state,
)


class GraduationTests(unittest.TestCase):
    def test_any_two_of_three_marks_graduated(self):
        self.assertTrue(is_graduated(GraduationCriteria(True, True, False)))
        self.assertTrue(is_graduated(GraduationCriteria(True, False, True)))
        self.assertTrue(is_graduated(GraduationCriteria(False, True, True)))

    def test_fewer_than_two_does_not_graduate(self):
        self.assertFalse(is_graduated(GraduationCriteria(True, False, False)))
        self.assertFalse(is_graduated(GraduationCriteria(False, False, False)))


class ArchivePageTests(unittest.TestCase):
    def test_archive_page_requires_both_detail_and_save_data_markers(self):
        self.assertTrue(is_archive_profile_page(["詳細資訊", "存檔資料", "67350 TB"]))
        self.assertFalse(is_archive_profile_page(["詳細資訊", "卡牌", "67350 TB"]))
        self.assertFalse(is_archive_profile_page(["存檔資料", "卡牌"]))

    def test_archive_page_marker_matching_ignores_whitespace(self):
        self.assertTrue(is_archive_profile_page(["詳細 資訊", "存檔 資料"]))

    def test_archive_snapshot_evaluates_two_configured_categories(self):
        targets = CharacterTargets(
            core_cards=["极光剑"], target_shines=["万众英雄"], finished_equipment=["蚀化臂铠"]
        )
        snapshot = ArchiveSnapshot(
            character_id="char-a", complete=True,
            cards=["极光剑", "万众英雄"], flashing_cards=["万众英雄"],
            equipment=["蚀化臂铠"],
        )
        self.assertEqual(
            GraduationCriteria(True, True, True),
            evaluate_archive_snapshot(snapshot, "char-a", targets),
        )

    def test_incomplete_or_wrong_character_snapshot_is_uncertain(self):
        targets = CharacterTargets(["极光剑"], ["万众英雄"], ["蚀化臂铠"])
        snapshot = ArchiveSnapshot("char-b", True, ["极光剑"], ["万众英雄"], ["蚀化臂铠"])
        self.assertIsNone(evaluate_archive_snapshot(snapshot, "char-a", targets))
        snapshot.character_id = "char-a"
        snapshot.complete = False
        self.assertIsNone(evaluate_archive_snapshot(snapshot, "char-a", targets))

    def test_unconfigured_target_category_stays_false(self):
        targets = CharacterTargets(core_cards=["极光剑"], target_shines=[], finished_equipment=["蚀化臂铠"])
        snapshot = ArchiveSnapshot("char-a", True, ["极光剑"], [], ["蚀化臂铠"])
        result = evaluate_archive_snapshot(snapshot, "char-a", targets)
        self.assertEqual(GraduationCriteria(True, False, True), result)


class QueueStateTests(unittest.TestCase):
    def setUp(self):
        self.queue = FarmQueue(
            profiles=[
                CharacterProfile(
                    "char-a", "角色甲",
                    zero_system=GraduationCriteria(True, True, True),
                    zero_system_targets=CharacterTargets(["极光剑"], ["万众英雄"], ["蚀化臂铠"]),
                    season_targets=CharacterTargets(core_cards=["赛季核心卡"]),
                ),
                CharacterProfile(
                    "char-b", "角色乙",
                    zero_system=GraduationCriteria(True, True, True),
                ),
            ],
            mode=FarmMode.ZERO_SYSTEM,
        )

    def test_queue_starts_at_first_ungraduated_character(self):
        self.assertEqual("char-a", self.queue.current_profile.character_id)
        self.assertEqual(QueueStatus.READY, self.queue.state.queue_status)

    def test_graduation_advances_queue_without_claiming_ui_switch(self):
        self.queue.start_current()
        self.queue.record_observation(RunObservation(True, True, False))
        self.assertEqual("char-b", self.queue.current_profile.character_id)
        self.assertEqual(QueueStatus.READY, self.queue.state.queue_status)
        self.assertEqual(RunStatus.GRADUATED, self.queue.state.runs["char-a"].status)

    def test_uncertain_observation_pauses_instead_of_advancing(self):
        self.queue.start_current()
        self.queue.record_observation(None)
        self.assertEqual(QueueStatus.PAUSED, self.queue.state.queue_status)
        self.assertEqual("char-a", self.queue.current_profile.character_id)
        self.assertEqual(RunStatus.PAUSED_UNCERTAIN, self.queue.state.runs["char-a"].status)

    def test_all_graduated_completes_queue(self):
        self.queue.start_current()
        self.queue.record_observation(RunObservation(True, True, False))
        self.queue.start_current()
        self.queue.record_observation(RunObservation(True, False, True))
        self.assertEqual(QueueStatus.COMPLETED, self.queue.state.queue_status)
        self.assertIsNone(self.queue.current_profile)

    def test_resume_retries_saved_character_after_pause(self):
        self.queue.start_current()
        self.queue.record_observation(None)
        self.queue.resume()
        self.assertEqual(QueueStatus.READY, self.queue.state.queue_status)
        self.assertEqual("char-a", self.queue.current_profile.character_id)

    def test_round_trip_persists_configuration_and_checkpoint(self):
        self.queue.start_current()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, self.queue.state)
            restored = load_state(path)
        self.assertEqual(self.queue.state, restored)

    def test_profiles_keep_separate_mode_targets(self):
        profile = self.queue.state.profiles[0]
        self.assertEqual(["极光剑"], profile.targets_for(FarmMode.ZERO_SYSTEM).core_cards)
        self.assertEqual(["赛季核心卡"], profile.targets_for(FarmMode.SEASON).core_cards)

    def test_bad_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_state(path)


if __name__ == "__main__":
    unittest.main()



