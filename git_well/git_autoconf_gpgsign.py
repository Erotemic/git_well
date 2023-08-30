#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK
import scriptconfig as scfg
import ubelt as ub


class GitAutoconfGpgsignCLI(scfg.DataConfig):
    __command__ = 'autoconf-gpg'

    remote = scfg.Value(None, help='param1')
    repo_dpath = scfg.Value('.', help='repo to set gpg for')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        """
        Example:
            >>> # xdoctest: +SKIP
            >>> from git_well.git_autoconf_gpgsign import *  # NOQA
            >>> cmdline = 0
            >>> kwargs = dict()
            >>> cls = GitAutoconfGpgsignCLI
            >>> cls.main(cmdline=cmdline, **kwargs)
        """
        import rich
        config = cls.cli(cmdline=cmdline, data=kwargs, strict=True)
        rich.print('config = ' + ub.urepr(config, nl=1))

        from git_well.repo import Repo
        repo = Repo.coerce(config.repo_dpath)

        import os
        env = os.environ.copy()
        env['GIT_SSH_COMMAND'] = 'ssh -v'

        infos = []
        for remote in repo.remotes:
            for url in list(remote.urls):
                print(f'url={url}')
                try:
                    info = repo.cmd(f'git ls-remote {url}', env=env)
                except Exception:
                    ...
                except KeyboardInterrupt:
                    ...
                else:
                    infos.append(info)

        identify_file_cands = ub.oset()
        for info in infos:
            for line in info.stderr.split('\n'):
                if 'identity file' in line:
                    cand = line.split(' ')[3]
                    cand = ub.Path(cand)
                    if cand.exists():
                        identify_file_cands.add(cand)

        email_candidates = ub.oset()
        for id_fpath in identify_file_cands:
            pub_key_fpath = id_fpath.augment(tail='.pub')
            if pub_key_fpath.exists():
                pub_email = pub_key_fpath.read_text().strip().split(' ')[-1]
                email_candidates.add(pub_email)

        gpg_candidates = []
        for email in email_candidates:
            # info = ub.cmd(f'gpg --list-keys --keyid-format LONG "{email}"')
            # print(info.stdout)
            # entries = gpg_entries(email)
            candidates = lookup_gpg_keyinfos(
                email, verbose=0, allow_mainkey=False, capabilities='sign',
                mintrust='u')
            gpg_candidates.extend(candidates)

        if len(gpg_candidates) != 1:
            print('gpg_candidates = {}'.format(ub.urepr(gpg_candidates, nl=1)))
            raise AssertionError('need to choose 1')

        # assert len(gpg_candidates) == 1
        fpr = gpg_candidates[0]['fpr']

        # https://help.github.com/en/articles/signing-commits
        repo.cmd('git config --local commit.gpgsign true')
        # Note the GPG key needs to match the email
        repo.cmd(f'git config --local user.email "{email}"')
        # Tell git which key to sign
        repo.cmd(f'git config --local user.signingkey "{fpr}"')

        print("CURRENT GLOBAL SETTINGS")
        repo.cmd(r'git config --global --list | grep "gpg\|sign\|email"', shell=True, verbose=1)

        print("CURRENT LOCAL SETTINGS")
        repo.cmd(r'git config --local --list | grep "gpg\|sign\|email"', shell=True, verbose=1)


def lookup_gpg_keyinfos(identifier, verbose=0, capabilities=None,
                        allow_subkey=True, allow_mainkey=True, full=True,
                        filter_expired=True, mintrust=None):
    """
    python ~/local/scripts/xgpg.py lookup_keyid "Emmy"
    python ~/local/scripts/xgpg.py lookup_keyid "Crall" --allow_mainkey=False --capabilities=sign
    python ~/local/scripts/xgpg.py lookup_keyid "Crall" --allow_mainkey=False --capabilities=encrypt
    python ~/local/scripts/xgpg.py lookup_keyid "Crall" --allow_mainkey=False --capabilities=auth
    python ~/local/scripts/xgpg.py lookup_keyid "Jonathan Crall"
    """
    if capabilities is None:
        capabilities = {'certify'}
    if isinstance(capabilities, str):
        capabilities = set(capabilities.split(','))

    entries = gpg_entries(identifier)

    # print(ub.repr2(entries, nl=2, sort=0))
    if verbose:
        import pandas as pd
        import rich
        # print('entries = {}'.format(ub.repr2(entries, nl=2)))
        for rows in entries:
            rows_df = pd.DataFrame(rows)
            rows_df.index.name = 'row'
            rich.print(rows_df.to_string())
            # print(ub.repr2(entries, nl=2, si=1, sort=0))

    want_caps = {c[0] for c in capabilities}
    candidates = []

    allowed_row_types = set()
    if allow_subkey:
        allowed_row_types.add('sub')
    if allow_mainkey:
        allowed_row_types.add('pub')

    for rows in entries:

        entry_uids = []
        entry_candidates = []
        trust = '-'
        for idx, row in enumerate(rows):
            row_ = {k: v for k, v in row.items() if v}
            if 'ownertrust' in row_:
                trust = row_['ownertrust']
            if row['type'] == 'uid':
                entry_uids.append(row_)
            elif row['type'] in allowed_row_types:
                have_caps = set(row.get('capabilities', ''))
                if filter_expired:
                    if row['valid'] == 'e':
                        continue
                if have_caps.issuperset(want_caps):
                    keyid = row['keyid']
                    if full:
                        # Find the full fingerprint
                        jdx = idx + 1
                        while jdx < len(rows) and rows[jdx]['type'] == 'fpr':
                            fpr_row = rows[jdx]
                            fpr = fpr_row['uid']
                            if fpr.endswith(keyid):
                                keyid = fpr
                                break
                            jdx += 1

                    keyinfo = {
                        'fpr': fpr,
                        'trust': trust,
                        'trust_level': TRUST_CODE_TO_LEVEL[trust],
                        **row_
                    }
                    entry_candidates.append(keyinfo)

            for keyinfo in entry_candidates:
                keyinfo['uids'] = entry_uids

        candidates.extend(entry_candidates)

    if len(candidates) == 0:
        raise Exception('no matches found for this query')

    if mintrust:
        mintrust_level = TRUST_CODE_TO_LEVEL[mintrust]
        candidates = [c for c in candidates if c.get('trust_level', 6) <= mintrust_level]

    return candidates


def gpg_entries(identifier=None, verbose=0):
    """
    References:
        # Format of the colon listings
        https://github.com/gpg/gnupg/blob/master/doc/DETAILS
    """
    suffix = ''
    if identifier is not None:
        suffix = ' ' + chr(34) + identifier + chr(34)
    info = ub.cmd('gpg --with-colons --fixed-list-mode --list-keys --keyid-format LONG' + suffix, verbose=verbose)

    default_field_info = {
        1: 'type',         # Field 1 - Type of record
        2: 'valid',        # Field 2 - Validity
        3: 'len',          # Field 3 - Key length
        4: 'pkalgo',       # Field 4 - Public key algorithm
        5: 'keyid',        # Field 5 - KeyID
        6: 'created',      # Field 6 - Creation Date
        7: 'expires',      # Field 7 - Expiration Date
        8: 'cert',
        9: 'ownertrust',
        10: 'uid',          # Field 10 - UserId
        11: 'sigclass',
        12: 'capabilities',
        13: 'issuer',
        14: 'flags',
        15: 'sn',
        16: 'hasher',
        17: 'curve',  }

    special_info = {
        'tru': {1: 'type', 2: 'stale', 3: 'trust', 4: 'date_create'},
        'pkd': {1: 'type', 2: 'index', 3: 'info', 4: 'value'},
        'cfg': {1: 'type'}}

    header = []
    entries = []
    current = None

    valid_lines = [line for line in info['out'].split(chr(10)) if line]
    for line in valid_lines:
        parts = line.split(':')
        rec_type = parts[0]
        if rec_type in special_info:
            field_info = special_info[rec_type]
        else:
            field_info = default_field_info
        record = {}
        for i, val in enumerate(parts, start=1):
            record[field_info.get(i, i)] = val
        if record['type'] == 'pub':
            if current is not None:
                entries.append(current)
            current = []
        if current is None:
            header.append(record)
        else:
            current.append(record)
    if current is not None:
        entries.append(current)
    return entries


TRUST_CODES = [
    # Not sure if all levels are correct
    {'code': '-', 'level': 4, 'desc': 'No ownertrust assigned / not yet calculated'},
    {'code': 'e', 'level': 4, 'desc': 'Trust calculation has failed; probably due to an expired key'},
    {'code': 'q', 'level': 4, 'desc': 'Not enough information for calculation'},
    {'code': 'n', 'level': 5, 'desc': 'Never trust this key'},
    {'code': 'm', 'level': 2, 'desc': 'Marginally trusted'},
    {'code': 'f', 'level': 1, 'desc': 'Fully trusted'},
    {'code': 'u', 'level': 0, 'desc': 'Ultimately trusted'},
]

TRUST_CODE_TO_LEVEL = {d['code']: d['level'] for d in TRUST_CODES}


__cli__ = GitAutoconfGpgsignCLI
main = __cli__.main

if __name__ == '__main__':
    """

    CommandLine:
        python ~/code/git_well/git_well/git_autoconf_gpgsign.py
        python -m git_well.git_autoconf_gpgsign
    """
    main()
