import dataclasses

import pytest

from fundtracker.model import Filing, FormDFiling, Issuer, Offering


def test_dataclasses_are_frozen_and_hashable():
    acc = "0002158817-26-000001"
    issuer = Issuer(acc, 1, True, "0002158817", "Axiaris Capital Management Fund, LP")
    filing = FormDFiling(Filing(acc, "0002158817", "2026-10-28", "D"), Offering(acc), (issuer,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        filing.filing.cik = "0"
    assert hash(filing) == hash(dataclasses.replace(filing))
    assert filing.accession == acc
    assert filing.primary_issuer is issuer
