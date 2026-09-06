from __future__ import annotations

import subprocess


def test_epoch_process_boundary_binary_encodes_string_stdin(monkeypatch):
    """String stdin must never reach subprocess text mode."""
    import git_well.epoch.core as epoch_core

    observed = {}

    def fake_run(argv, **kwargs):
        observed['argv'] = argv
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=b'ok\r\n',
            stderr=b'',
        )

    monkeypatch.setattr(epoch_core.subprocess, 'run', fake_run)
    payload = 'update refs/heads/main new old\n'
    result = epoch_core._run(
        ['git', 'update-ref', '--stdin'],
        input=payload,
    )

    assert observed['input'] == payload.encode()
    assert observed['text'] is False
    assert b'\r\n' not in observed['input']
    assert result.stdout == 'ok\n'


def test_epoch_process_boundary_preserves_explicit_binary_mode(monkeypatch):
    import git_well.epoch.core as epoch_core

    observed = {}

    def fake_run(argv, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=b'ok', stderr=b'')

    monkeypatch.setattr(epoch_core.subprocess, 'run', fake_run)
    payload = b'raw\x00bytes\n'
    result = epoch_core._run(['git', 'mktree', '-z'], input=payload, text=False)

    assert observed['input'] == payload
    assert observed['text'] is False
    assert result.stdout == b'ok'
