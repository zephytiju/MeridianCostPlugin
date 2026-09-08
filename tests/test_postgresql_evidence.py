# SPDX-License-Identifier: Apache-2.0
"""A host transaction commits Cost records and required Evidence together."""

import os
from uuid import uuid4

import pytest

from conftest import END, NOW, START, make_rate_card, make_usage_input
from evidence_support import RECEIPTS
from meridian_storage import OperationContext
from meridian_storage.errors import MeridianError
from meridian_storage.plugins.cost import (
    CalculationV1,
    CostCalculator,
    CostRepository,
    StaticUsageProvider,
)
from postgres_backend import LiveBackend


class RequiredReceipts:
    """Application integration using Cost's public evidence sink protocol."""

    def __init__(self, runtime, failure):
        self.runtime = runtime
        self.failure = failure
        self.calculation = None
        self.records = ()

    def emit(self, calculation, records):
        self.calculation, self.records = calculation, records
        for kind in ("audit", "lineage"):
            data = {
                "evidenceId": f"{kind}:{calculation.fingerprint}",
                "observedTime": calculation.created_at.isoformat(),
                "payload": {
                    "kind": kind,
                    "calculation": calculation.to_dict(),
                    "records": [item.to_dict() for item in records],
                },
                "count": 1,
            }
            self.runtime.execute(
                self.runtime.catalog("evidence").append(
                    resource=RECEIPTS,
                    data={} if self.failure == kind else data,
                    require_atomic=True,
                )
            )


@pytest.mark.integration
@pytest.mark.parametrize("failure", [None, "audit", "lineage"])
def test_required_cost_audit_lineage_atomicity_and_restart(failure):
    dsn = os.environ.get("COST_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.fail("COST_TEST_POSTGRES_DSN is required; live acceptance cannot be skipped")
    backend = LiveBackend(dsn, evidence=True)
    context = OperationContext("test:cost", tenant=uuid4().hex, scope={"runtime": "cost"})
    try:
        runtime = backend.start()
        with runtime.context(context):
            cost = CostRepository(runtime)
            draft, _ = cost.create_rate_card(make_rate_card())
            validated = cost.validate_rate_card(draft.rate_card_id, 1, expected_revision=1, now=NOW)
            card = cost.publish_rate_card(
                draft.rate_card_id, 1, expected_revision=validated.revision, now=NOW
            )
            sink = RequiredReceipts(runtime, failure)
            calculator = CostCalculator(
                StaticUsageProvider(make_usage_input()), cost, evidence=sink
            )

            def calculate():
                # The host owns the required atomic boundary. Cost's internal
                # transaction joins the same Binding and request owner.
                with runtime.transaction(cost.resources.calculations):
                    return calculator.calculate(
                        scope={"tenant": "acme"}, start=START, end=END, rate_card=card.ref
                    )

            if failure:
                with pytest.raises(MeridianError):
                    calculate()
            else:
                result = calculate()
                assert calculate().replayed
        runtime.close()
        fresh = backend.start()
        with fresh.context(context):
            cost = CostRepository(fresh)
            rows = fresh.execute(fresh.catalog("evidence").query(resource=RECEIPTS)).data["items"]
            stored = cost.get_calculation(sink.calculation.calculation_id, 1)
            if failure:
                assert stored is None
                assert not rows
                assert all(cost.get_cost_record(record.cost_id) is None for record in sink.records)
            else:
                assert stored.to_dict() == result.calculation.to_dict()
                assert len(rows) == 2
                assert {row["payload"]["kind"] for row in rows} == {"audit", "lineage"}
                for row in rows:
                    assert (
                        CalculationV1.from_mapping(row["payload"]["calculation"]).to_dict()
                        == stored.to_dict()
                    )
                assert all(
                    cost.get_cost_record(record.cost_id).to_dict() == record.to_dict()
                    for record in result.records
                )
    finally:
        backend.close()
