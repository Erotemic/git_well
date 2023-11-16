import scriptconfig as scfg
from _typeshed import Incomplete


class GitAutoconfGpgsignCLI(scfg.DataConfig):
    __command__: str
    remote: Incomplete
    repo_dpath: Incomplete

    @classmethod
    def main(cls, cmdline: int = ..., **kwargs) -> None:
        ...


def lookup_gpg_keyinfos(identifier,
                        verbose: int = ...,
                        capabilities: Incomplete | None = ...,
                        allow_subkey: bool = ...,
                        allow_mainkey: bool = ...,
                        full: bool = ...,
                        filter_expired: bool = ...,
                        mintrust: Incomplete | None = ...):
    ...


def gpg_entries(identifier: Incomplete | None = ..., verbose: int = ...):
    ...


TRUST_CODES: Incomplete
TRUST_CODE_TO_LEVEL: Incomplete
__cli__ = GitAutoconfGpgsignCLI
main: Incomplete
