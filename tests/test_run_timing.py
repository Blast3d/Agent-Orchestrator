import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from run_timing import summarize

class RunTimingTests(unittest.TestCase):
    def row(self, start, end, duration):
        return dict(started_at=f'2026-09-26T05:00:{start:02d}+00:00', ended_at=f'2026-09-26T05:00:{end:02d}+00:00', phase_durations_ms={'provider_execution':duration*1000,'memory_lookup':500})
    def test_parallel_time_is_not_wall_time(self):
        value=summarize([self.row(0,10,10),self.row(5,15,10)])
        self.assertEqual(value['execution_sum_seconds'],20)
        self.assertEqual(value['observed_span_seconds'],15)
        self.assertEqual(value['observed_peak_workers'],2)
        self.assertEqual(value['recall_sum_seconds'],1)
    def test_touching_intervals_do_not_overlap(self):
        self.assertEqual(summarize([self.row(0,5,5),self.row(5,10,5)])['observed_peak_workers'],1)
    def test_unknown_and_import_time_stay_unknown(self):
        data=[{},dict(self.row(0,5,5),imported_completed_artifact=True)]
        value=summarize(data)
        self.assertEqual(value['hosted_tasks'],1)
        self.assertIsNone(value['execution_sum_seconds'])
        self.assertIsNone(value['observed_peak_workers'])
    def test_clock_jump_not_used_for_overlap(self):
        value=summarize([self.row(0,45,5)])
        self.assertEqual(value['execution_sum_seconds'],5)
        self.assertIsNone(value['observed_peak_workers'])
    def test_invalid_measurements_are_not_zero(self):
        value=summarize([{'phase_durations_ms':{'provider_execution':float('inf'),'memory_lookup':True},'started_at':'invalid','ended_at':None}])
        self.assertIsNone(value['execution_sum_seconds'])
        self.assertIsNone(value['recall_sum_seconds'])

    def test_aggregate_overflow_remains_unknown(self):
        value=summarize([{'phase_durations_ms':{'provider_execution':1e308,'memory_lookup':1e308}}]*10000)
        self.assertIsNone(value['execution_sum_seconds'])
        self.assertIsNone(value['recall_sum_seconds'])
