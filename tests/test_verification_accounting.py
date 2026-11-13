"""Incremental evidence preserves actual canonical accounting and immutable pins."""
import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import flow_api_delivery as api
from aim_ci import verification_runtime as runtime
import test_api_delivery as fixtures


class VerificationAccountingTests(fixtures.ApiDeliveryTests):
    def witness(self, path, *, campaign=True):
        return {'path': str(path), 'content': True, 'directory': False, 'campaign': campaign,
            'value': runtime._shape(str(path), content=True, campaign=campaign)}

    def test_real_campaign_append_is_reused_but_changed_prefix_and_raw_pin_are_not(self):
        account = api.Campaign(self.c)
        budget = account.directory / 'budget.json'
        raw_witness = self.witness(budget, campaign=False)
        closure = {'files': [self.witness(budget)], 'queries': []}
        self.assertTrue(runtime._valid(closure))
        account.reserve('1'*64, '2'*64, 80, 1000, self.base / 'future-operation')
        account.finish('1'*64, self.answer)
        self.assertTrue(runtime._valid(closure))
        self.assertFalse(runtime._valid({'files': [raw_witness], 'queries': []}))
        closure = {'files': [self.witness(budget)], 'queries': []}
        original = api.file_bytes(budget)
        data = account.read(); data['requests'][0]['requestSha256'] = '3'*64
        api.atomic_json(budget, data)
        self.assertFalse(runtime._valid(closure))
        budget.write_bytes(original)
        data = account.read(); data['requests'] = []
        api.atomic_json(budget, data)
        self.assertFalse(runtime._valid(closure))

    def test_appending_an_attempt_inside_previously_verified_producer_state_invalidates_reuse(self):
        account = api.Campaign(self.c)
        budget = account.directory / 'budget.json'
        producer = self.base / 'producer'; producer.mkdir()
        receipt = producer / 'candidate-receipt.json'; receipt.write_text('{}')
        closure = {'files': [self.witness(budget), self.witness(receipt, campaign=False)], 'queries': []}
        account.reserve('1'*64, '2'*64, 80, 1000, producer / 'new-paid-attempt')
        account.finish('1'*64, self.answer)
        self.assertFalse(runtime._valid(closure))


for name in dir(fixtures.ApiDeliveryTests):
    if name.startswith('test_') and name not in VerificationAccountingTests.__dict__:
        setattr(VerificationAccountingTests, name, None)
