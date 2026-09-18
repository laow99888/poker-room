"""Independent acceptance checks for permutations, dead blinds and short stacks."""
import copy
import random
import pytest
from test_session_regressions import payload, post, client


def test_m10_participant_permutations_replay_identically():
    body = payload(6)
    body['ops'] = [{'op': 'action', 'seat_id': s, 'type': 'call'} for s in [3, 4, 5]]
    expected = post('/api/hand/v2/view', body)
    mapping = post('/api/table/prepare', body)['mapping']
    rng = random.Random(17)
    for _ in range(12):
        rng.shuffle(body['context']['participants'])
        assert post('/api/hand/v2/view', body) == expected
        assert post('/api/table/prepare', body)['mapping'] == mapping


@pytest.mark.parametrize('n', range(3, 9))
def test_p10_empty_small_blind_charges_only_actual_players(n):
    body = payload(n, no_sb=True)
    body['context']['blinds']['ante_each'] = 25
    view = post('/api/hand/v2/view', body)
    assert view['pot'] == 200 + n * 25
    assert len(view['seats']) == n
    assert view['actor_seat_id'] == 3
    stacks = {s['seat_id']: s['stack'] for s in view['seats']}
    assert stacks[2] == 9775
    assert all(value == 9975 for seat, value in stacks.items() if seat != 2)


def test_p13_full_nine_player_table_cannot_have_no_small_blind():
    body = payload(9)
    body['context']['small_blind_seat_id'] = None
    response = client.post('/api/table/prepare', json=body)
    assert 400 <= response.status_code < 500


@pytest.mark.parametrize('seat,chips,pot', [(1, 50, 250), (2, 75, 175)])
def test_p15_player_below_blind_remains_in_hand(seat, chips, pot):
    body = payload(6)
    body['context']['participants'][seat-1]['starting_chips'] = chips
    view = post('/api/hand/v2/view', body)
    short = next(s for s in view['seats'] if s['seat_id'] == seat)
    assert short['stack'] == 0 and not short['folded']
    assert len(view['seats']) == 6
    assert view['pot'] == pot


@pytest.mark.parametrize('op', [
    {'op':'action','seat_id':42,'type':'fold'},
    {'op':'action','seat_id':3,'seat':0,'type':'fold'},
    {'op':'action','seat_id':3,'index':0,'type':'fold'},
    {'op':'action','seat_id':'3','type':'fold'},
])
def test_a14_bad_actor_never_defaults_or_500s(op):
    body = payload(6)
    body['ops'] = [copy.deepcopy(op)]
    response = client.post('/api/hand/v2/view', json=body)
    assert 400 <= response.status_code < 500
