"""Explicit budget increase keeps old verifiers and every historical liability intact."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_api_delivery as fixtures
from aim_ci.owned_paths import Refused
a = fixtures.a


class CampaignContinuationTests(fixtures.ApiDeliveryTests):
    def setup_continuation(self):
        self.execute()
        self.previous = {'campaignDirectory': self.c['campaignDirectory'], 'historicalBudget': self.c['historicalBudget']}
        self.old = Path(self.previous['campaignDirectory'])
        self.old_ledger = (self.old / 'budget.json').read_bytes()
        self.baseline = self.base / 'prior-budget.json'; self.baseline.write_bytes(self.old_ledger)
        self.extension_path = self.base / 'continuation.json'
        self.extension = {'format': 'aim-flow-campaign-continuation', 'schemaVersion': 1,
            'campaignDirectory': str(self.base / 'new-campaign'), 'limitNanoUsd': 30_000_000_000,
            'priorCampaign': self.previous, 'priorBudgetSnapshot': self.pin(self.baseline)}
        self.extension_path.write_bytes(a.encoded(self.extension))
        self.c = a.funded_config(self.c, self.pin(self.extension_path))
        self.payload['input'] += ' New separately granted continuation.'
        self.req.write_bytes(a.encoded(self.payload)); self.c['request'] = self.pin(self.req)
        self.state = self.base / 'new-operation'; self.save()
        self.answer['id'] = 'resp_synthetic_extension'

    def test_new_dispatch_retains_old_bytes_and_exposes_exact_combined_limit(self):
        self.setup_continuation()
        original_state = self.state; self.state = self.old / 'must-not-create'
        with self.assertRaises(Refused): self.preview()
        self.assertFalse(self.state.exists()); self.state = original_state
        preview = self.preview()
        self.assertEqual(preview['intent']['limitNanoUsd'], 30_000_000_000)
        self.assertFalse(Path(self.c['campaignDirectory']).exists())
        result = self.execute()
        self.assertEqual(result['status'], 'proposal-ready-for-independent-review', result)
        ledger = a.read_existing_campaign(self.c)
        self.assertEqual(ledger['identity']['allocatedPriorCampaignNanoUsd'], 20_000_000_000)
        self.assertEqual(len(ledger['requests']), 1)
        self.assertEqual((self.old / 'budget.json').read_bytes(), self.old_ledger)
        self.assertEqual(len(a.read_existing_campaign(self.previous)['requests']), 1)
        # A second authority/directory cannot duplicate the top-up allowance.
        changed = copy.deepcopy(self.extension); changed['campaignDirectory'] = str(self.base / 'another-campaign')
        other = self.base / 'other.json'; other.write_bytes(a.encoded(changed))
        with self.assertRaisesRegex(Refused, 'authority changed'):
            a.authority(a.funded_config(self.previous, self.pin(other)))

    def test_prior_full_allocation_bounds_new_spend_and_old_future_writes_remain_valid(self):
        self.setup_continuation(); campaign = a.Campaign(self.c)
        for n in range(2): campaign.reserve('new' + str(n), 'request' + str(n), 800000, 64000, self.base / str(n))
        with self.assertRaisesRegex(Refused, 'ceiling'):
            campaign.reserve('excess', 'excess-request', 100000, 128000, self.base / 'excess')
        a.Campaign(self.previous).reserve('old-future', 'old-future-request', 800000, 64000, self.base / 'old-future')
        current = campaign.read()
        self.assertLessEqual(20_000_000_000 + sum(r['reservedNanoUsd'] for r in current['requests']), 30_000_000_000)
        self.assertEqual(len(a.read_existing_campaign(self.previous)['requests']), 2)

    def test_old_requests_cannot_be_replayed_and_tampered_prefix_is_refused(self):
        self.setup_continuation(); campaign = a.Campaign(self.c)
        row = a.read_existing_campaign(self.previous)['requests'][0]
        with self.assertRaisesRegex(Refused, 'never replay'):
            campaign.reserve('different-id', row['requestSha256'], 80, 1000, self.base / 'duplicate')
        ledger = json.loads(self.old_ledger); ledger['requests'] = []
        (self.old / 'budget.json').write_bytes(a.encoded(ledger))
        with self.assertRaisesRegex(Refused, 'snapshot changed'): campaign.read()
        (self.old / 'budget.json').write_bytes(self.old_ledger)
        self.baseline.write_bytes(self.baseline.read_bytes() + b' ')
        with self.assertRaisesRegex(Refused, 'Pinned input changed'): campaign.read()

    def test_unsettled_snapshot_and_missing_established_files_are_not_repaired(self):
        self.setup_continuation(); old_campaign = a.Campaign(self.previous)
        old_campaign.reserve('in-flight', 'in-flight-request', 80, 1000, self.base / 'pending')
        self.baseline.write_bytes((self.old / 'budget.json').read_bytes())
        self.extension['priorBudgetSnapshot'] = self.pin(self.baseline)
        self.extension_path.write_bytes(a.encoded(self.extension))
        with self.assertRaisesRegex(Refused, 'unsettled'):
            a.funded_config(self.previous, self.pin(self.extension_path))
        (self.old / 'budget.json').write_bytes(self.old_ledger)
        self.baseline.write_bytes(self.old_ledger); self.extension['priorBudgetSnapshot'] = self.pin(self.baseline)
        self.extension_path.write_bytes(a.encoded(self.extension)); self.c = a.funded_config(self.previous, self.pin(self.extension_path))
        campaign = a.Campaign(self.c); marker = Path(self.c['campaignDirectory']) / 'campaign.json'; marker.unlink()
        with self.assertRaises((Refused, OSError)): a.Campaign(self.c)
        self.assertFalse(marker.exists())

    def setup_second_continuation(self):
        self.setup_continuation(); self.execute()
        self.first_config = {key: self.c[key] for key in
            ('campaignDirectory', 'historicalBudget', 'campaignContinuation')}
        self.first_directory = Path(self.c['campaignDirectory'])
        self.first_bytes = (self.first_directory / 'budget.json').read_bytes()
        snapshot = self.base / 'second-prior-budget.json'; snapshot.write_bytes(self.first_bytes)
        self.second_path = self.base / 'second-continuation.json'
        self.second = {'format': 'aim-flow-campaign-continuation', 'schemaVersion': 2,
            'campaignDirectory': str(self.base / 'second-campaign'), 'limitNanoUsd': 40_000_000_000,
            'priorCampaign': self.first_config, 'priorBudgetSnapshot': self.pin(snapshot),
            'maximumPaidGenerations': 3}
        self.second_path.write_bytes(a.encoded(self.second))
        self.c = a.funded_config(self.c, self.pin(self.second_path))
        self.state = self.base / 'second-operation'
        self.payload['input'] += ' Explicit second funding increase.'
        self.req.write_bytes(a.encoded(self.payload)); self.c['request'] = self.pin(self.req)
        self.save()

    def test_second_increase_preserves_all_allocations_and_blocks_every_ancestor_replay(self):
        self.setup_second_continuation()
        preview = self.preview()
        self.assertEqual(preview['intent']['limitNanoUsd'], 40_000_000_000)
        self.assertEqual(self.execute()['status'], 'proposal-ready-for-independent-review')
        campaign = a.Campaign(self.c)
        self.assertEqual(campaign.read()['identity']['allocatedPriorCampaignNanoUsd'], 30_000_000_000)
        for ancestor in (self.previous, self.first_config):
            row = a.read_existing_campaign(ancestor)['requests'][0]
            with self.assertRaisesRegex(Refused, 'never replay'):
                campaign.reserve('new-id', row['requestSha256'], 80, 1000, self.base / 'duplicate')
        self.assertEqual((self.old / 'budget.json').read_bytes(), self.old_ledger)
        self.assertEqual((self.first_directory / 'budget.json').read_bytes(), self.first_bytes)
        self.assertEqual(set(a.prior_campaign_roots(self.c)), {self.old, self.first_directory})
        # One continuation per exact prior identity, even for a second increase.
        changed = copy.deepcopy(self.second); changed['campaignDirectory'] = str(self.base / 'duplicate-top-up')
        other = self.base / 'duplicate-second.json'; other.write_bytes(a.encoded(changed))
        with self.assertRaisesRegex(Refused, 'authority changed'):
            a.authority(a.funded_config(self.first_config, self.pin(other)))

    def test_second_generation_bound_counts_unknown_and_never_refunds_or_exceeds_ceiling(self):
        self.setup_second_continuation(); campaign = a.Campaign(self.c)
        for n in range(3):
            campaign.reserve('bounded' + str(n), 'bounded-request' + str(n), 80, 1000, self.base / str(n))
        campaign.finish('bounded0', None, 'transport outcome unknown')
        before = (campaign.directory / 'budget.json').read_bytes()
        with self.assertRaisesRegex(Refused, 'generation bound exhausted'):
            campaign.reserve('fourth', 'fourth-request', 80, 1000, self.base / 'fourth')
        self.assertEqual((campaign.directory / 'budget.json').read_bytes(), before)
        self.assertEqual(campaign.read()['requests'][0]['status'], 'unknown')
    def test_second_increase_money_ceiling_still_blocks_before_generation_limit(self):
        self.setup_second_continuation(); campaign = a.Campaign(self.c)
        campaign.reserve('large1', 'large-request1', 922000, 128000, self.base / 'large1')
        with self.assertRaisesRegex(Refused, 'ceiling'):
            campaign.reserve('large2', 'large-request2', 922000, 128000, self.base / 'large2')
        self.assertEqual(len(campaign.read()['requests']), 1)

    def test_second_increase_rejects_nonincrease_overlap_and_invalid_generation_count(self):
        self.setup_second_continuation()
        for field, value, message in (
            ('limitNanoUsd', 30_000_000_000, 'strictly increase'),
            ('campaignDirectory', str(self.old), 'cyclic or overlaps'),
            ('maximumPaidGenerations', 0, 'generation bound'),
            ('maximumPaidGenerations', -1, 'generation bound'),
            ('maximumPaidGenerations', True, 'generation bound')):
            broken = copy.deepcopy(self.second); broken[field] = value
            path = self.base / 'invalid-second.json'; path.write_bytes(a.encoded(broken))
            with self.subTest(field=field, value=value), self.assertRaisesRegex(Refused, message):
                a.funded_config(self.first_config, self.pin(path))

    def test_explicit_six_generation_authority_is_accepted_and_enforced(self):
        self.setup_second_continuation()
        self.second['maximumPaidGenerations'] = 6
        self.second_path.write_bytes(a.encoded(self.second))
        self.c = a.funded_config(self.first_config, self.pin(self.second_path))
        campaign = a.Campaign(self.c)
        for n in range(6):
            campaign.reserve('six-' + str(n), 'six-request-' + str(n), 80, 1000, self.base / str(n))
        with self.assertRaisesRegex(Refused, 'generation bound exhausted'):
            campaign.reserve('seventh', 'seventh-request', 80, 1000, self.base / 'seventh')
        self.assertEqual(len(campaign.read()['requests']), 6)

    def test_continuation_depth_is_not_an_arbitrary_product_limit(self):
        self.setup_second_continuation()
        # Declaration validation is independent of ledger initialization. Each pin
        # points to a distinct explicitly increased authority, never a cycle.
        current = self.c
        for index in range(4):
            spec = {**self.second, 'campaignDirectory': str(self.base / ('extension-' + str(index))),
                'limitNanoUsd': (50 + 10 * index) * 1_000_000_000,
                'priorCampaign': {k: current[k] for k in
                    ('campaignDirectory', 'historicalBudget', 'campaignContinuation')}}
            path = self.base / ('extension-' + str(index) + '.json')
            path.write_bytes(a.encoded(spec))
            current = {**current, 'campaignDirectory': spec['campaignDirectory'],
                'campaignContinuation': self.pin(path)}
        self.assertEqual(len(a.continuation_declarations(current)), 6)

    def test_second_increase_allows_future_prior_spend_but_retains_tamper_detection(self):
        self.setup_second_continuation(); campaign = a.Campaign(self.c)
        a.Campaign(self.previous).reserve('older-future', 'older-future-request', 80, 1000, self.base / 'older')
        a.Campaign(self.first_config).reserve('prior-future', 'prior-future-request', 80, 1000, self.base / 'prior')
        campaign.reserve('new-future', 'new-future-request', 80, 1000, self.base / 'new')
        self.assertLessEqual(30_000_000_000 + sum(r['reservedNanoUsd'] for r in campaign.read()['requests']), 40_000_000_000)
        broken = json.loads(self.old_ledger); broken['requests'] = []
        (self.old / 'budget.json').write_bytes(a.encoded(broken))
        with self.assertRaisesRegex(Refused, 'snapshot changed'):
            campaign.read()


for name in dir(fixtures.ApiDeliveryTests):
    if name.startswith('test_') and name not in CampaignContinuationTests.__dict__:
        setattr(CampaignContinuationTests, name, None)

if __name__ == '__main__': unittest.main()
