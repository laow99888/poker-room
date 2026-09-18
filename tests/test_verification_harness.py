"""Verify that the acceptance harness catches failure and isolation regressions."""
import os
import subprocess
import sys
import pytest
import _verify_all
from backend import opponents
from test_opponents import test_user_namespaces_isolate_profiles as check_namespaces
from test_decision_api import test_stats_reset_default_is_self_only as check_reset


def test_verification_exit_is_nonzero_if_any_suite_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['_verify_all.py', '--browser'])
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 7 if len(calls) == 1 else 0)
    monkeypatch.setattr(subprocess, 'run', run)
    assert _verify_all.main() == 1
    assert len(calls) == 4
    assert calls[-1] == ['node', 'tests/browser/acceptance.e2e.mjs']


def test_namespace_regression_detects_reset_all_mutation(monkeypatch, isolated_stats):
    called = []
    def wrong_reset(uid):
        called.append(uid)
        return opponents.reset_all()
    monkeypatch.setattr(opponents, 'reset_user', wrong_reset)
    with pytest.raises(AssertionError):
        check_namespaces(isolated_stats)
    assert called == ['user-A']


@pytest.mark.parametrize('initial', [None, 'qa-original-value'])
def test_reset_test_restores_both_environment_cases(monkeypatch, initial):
    if initial is None:
        monkeypatch.delenv('POKER_ADMIN_TOKEN', raising=False)
    else:
        monkeypatch.setenv('POKER_ADMIN_TOKEN', initial)
    with pytest.MonkeyPatch.context() as inner:
        check_reset(inner)
    assert os.environ.get('POKER_ADMIN_TOKEN') == initial
