import tempfile
import unittest
from pathlib import Path

from ok_tasks.czn_farm import (
    CharacterProfile,
    FarmMode,
    FarmQueue,
    GraduationCriteria,
    QueueStatus,
    RunObservation,
    RunStatus,
    SessionState,
    is_graduated,
    is_archive_profile_page,
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


class QueueStateTests(unittest.TestCase):
    def setUp(self):
        self.queue = FarmQueue(
            profiles=[
                CharacterProfile(
                    "char-a", "角色甲",
                    zero_system=GraduationCriteria(True, True, True),
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

    def test_bad_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_state(path)


if __name__ == "__main__":
    unittest.main()


