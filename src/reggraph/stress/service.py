"""Running and checking stress tests.

====  ======================================================================================
R1    A run freezes its inputs: positions (aggregated and converted), covers, group members,
      exchange rates, capital buffers and the scenario version as run. The result is computed
      from that frozen snapshot only.
R2    ``max_batch`` is always made concrete (the newest batch when the caller gives none), so
      "which data did this use" has one answer.
R3    The run is signed over (tenant, run id, scenario@version, input hash, result hash).
R4    ``verify`` recomputes the result from the stored snapshot and checks every hash and the
      signature. Later data, rates or contract changes cannot affect an old run.
R5    Running needs stress:write plus read access to exposure, insurance and registry data.
====  ======================================================================================
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict

from reggraph.core.clock import Clock
from reggraph.core.errors import ValidationFailure
from reggraph.currency import Converter, Money, parse_money_amount
from reggraph.exposure.service import FxUsed, active_currency
from reggraph.exposure.store import ExposureStore
from reggraph.insurance import ContractStore
from reggraph.registry.lei import validate_lei
from reggraph.registry.store import EntityStore
from reggraph.security.rbac import Permission, Principal
from reggraph.security.signing import ResultSigner
from reggraph.storage.base import Db
from reggraph.stress import engine
from reggraph.stress.engine import SnapCover, SnapRow, Snapshot, StressResult
from reggraph.stress.models import DefaultShock, ScenarioInput, canonical, sha256
from reggraph.stress.store import MAX_RUNS_PER_TENANT, RunRecord, StoredRun, StressStore

MAX_ROWS: Final = 20_000
MAX_BUFFERS: Final = 200


class RunView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: RunRecord
    result: StressResult


class Verification(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: int
    input_hash_ok: bool
    result_reproduced: bool
    result_hash_ok: bool
    signature_ok: bool
    scenario_matches_version: bool
    ok: bool


def _message(tenant: str, rec: RunRecord) -> str:
    return (
        f"{tenant}|{rec.run_id}|{rec.scenario_key}@{rec.scenario_version}"
        f"|{rec.input_hash}|{rec.result_hash}"
    )


class StressLab:
    def __init__(
        self,
        db: Db,
        clock: Clock,
        store: StressStore,
        exposures: ExposureStore,
        contracts: ContractStore,
        registry: EntityStore,
        signer: ResultSigner,
        converter_for: Callable[[str], Converter],
    ) -> None:
        self._db = db
        self._clock = clock
        self._store = store
        self._exposures = exposures
        self._contracts = contracts
        self._registry = registry
        self._signer = signer
        self._converter_for = converter_for

    # ------------------------------------------------------------------ run
    def run(
        self,
        who: Principal,
        scenario_key: str,
        *,
        version: int | None = None,
        on: date,
        currency: str = "EUR",
        buffers: dict[str, str] | None = None,
        max_batch: int | None = None,
    ) -> RunView:
        for perm in (
            Permission.STRESS_WRITE,
            Permission.EXPOSURE_READ,
            Permission.INSURANCE_READ,
            Permission.REGISTRY_READ,
        ):
            who.require(perm)
        sv = self._store.get_scenario(who, scenario_key, version)
        scenario = sv.scenario
        cur = active_currency(currency)
        tenant = who.tenant_id

        named = scenario.named_leis()
        if named - self._registry.existing_leis(tenant, named):
            raise ValidationFailure("the scenario names LEIs that are not registered")
        limit = max_batch if max_batch is not None else self._exposures.latest_batch_id(who)

        snapshot = self._snapshot(who, scenario, on, cur, limit)
        buffer_values = self._buffers(buffers or {}, cur)

        scenario_obj = scenario.model_dump(mode="json")
        inputs_obj = {
            "snapshot": snapshot.model_dump(mode="json"),
            "buffers": {k: str(v) for k, v in sorted(buffer_values.items())},
        }
        input_hash = sha256(canonical({"scenario": scenario_obj, "inputs": inputs_obj}))
        result = engine.run(scenario, snapshot, buffer_values)
        result_json = canonical(result.model_dump(mode="json"))
        result_hash = sha256(result_json)

        with self._db.transaction():
            if self._store.count_runs(tenant) >= MAX_RUNS_PER_TENANT:
                raise ValidationFailure("stress run limit reached")
            record = RunRecord(
                run_id=self._store.next_run_id(tenant),
                scenario_key=sv.scenario_key,
                scenario_version=sv.version_no,
                as_of=on.isoformat(),
                currency=cur,
                max_batch=limit,
                input_hash=input_hash,
                result_hash=result_hash,
                signature="",
                created_by=f"user:{who.user_id}",
                created_at=self._clock.now(),
            )
            record = record.model_copy(
                update={"signature": self._signer.sign(_message(tenant, record))}
            )
            self._store.save_run(
                who,
                record,
                scenario_json=canonical(scenario_obj),
                inputs_json=canonical(inputs_obj),
                result_json=result_json,
            )
        return RunView(run=record, result=result)

    def _buffers(self, raw: dict[str, str], currency: str) -> dict[str, Decimal]:
        if len(raw) > MAX_BUFFERS:
            raise ValidationFailure("too many capital buffers")
        return {validate_lei(k): parse_money_amount(v, currency) for k, v in raw.items()}

    def _snapshot(
        self, who: Principal, scenario: ScenarioInput, on: date, cur: str, max_batch: int
    ) -> Snapshot:
        tenant = who.tenant_id
        converter = self._converter_for(tenant)
        positions = self._exposures.positions_as_of(who, on, max_batch=max_batch)
        fx: dict[str, FxUsed] = {}
        summed: dict[tuple[str, str, str, str], Decimal] = {}
        for item in positions:
            p = item.position
            conv = converter.convert(Money(p.amount, p.currency), cur, on)
            fx.setdefault(
                p.currency, FxUsed(currency=p.currency, rate=conv.rate, rate_date=conv.rate_date)
            )
            key = (p.holder_lei, p.counterparty_lei, p.exposure_type.value, p.currency)
            summed[key] = summed.get(key, Decimal(0)) + conv.result.amount
        if len(summed) > MAX_ROWS:
            raise ValidationFailure("too many distinct exposures for one stress run")
        jurisdictions = self._registry.jurisdictions_of(tenant, {k[1] for k in summed})
        rows = [
            SnapRow(
                holder_lei=h,
                counterparty_lei=c,
                counterparty_jurisdiction=jurisdictions.get(c, ""),
                exposure_type=t,
                currency=ccy,
                amount=amount,
            )
            for (h, c, t, ccy), amount in sorted(summed.items())
        ]
        covers: list[SnapCover] = []
        contract_lineage: list[dict[str, object]] = []
        for cv in self._contracts.all_current(who):
            c = cv.contract
            if not c.active_on(on) or c.line.value == "reinsurance":
                continue
            conv = converter.convert(Money(c.sum_insured, c.currency), cur, on)
            fx.setdefault(
                c.currency, FxUsed(currency=c.currency, rate=conv.rate, rate_date=conv.rate_date)
            )
            covers.append(
                SnapCover(
                    contract_ref=cv.contract_ref,
                    version_no=cv.version_no,
                    insurer_lei=c.insurer_lei,
                    insured_lei=c.insured_lei,
                    amount=conv.result.amount,
                )
            )
            contract_lineage.append(
                {
                    "contract_ref": cv.contract_ref,
                    "version_no": cv.version_no,
                    "hash": cv.content_hash,
                }
            )
        members: dict[str, list[str]] = {}
        for shock in scenario.shocks:
            if isinstance(shock, DefaultShock) and shock.include_group:
                for lei in shock.leis:
                    group = self._registry.group(who, lei, on)
                    members[lei] = sorted(m.lei for m in group.members)
        batch_ids = sorted({i.batch_id for i in positions})
        lineage: dict[str, object] = {
            "batch_ids": batch_ids,
            "batch_hashes": [self._exposures.get_batch(who, b).content_hash for b in batch_ids],
            "contracts": sorted(contract_lineage, key=lambda d: str(d["contract_ref"])),
            "fx": [
                f.model_dump(mode="json") for f in sorted(fx.values(), key=lambda f: f.currency)
            ],
        }
        return Snapshot(
            as_of=on.isoformat(),
            currency=cur,
            max_batch=max_batch,
            rows=rows,
            covers=sorted(covers, key=lambda c: c.contract_ref),
            group_members=members,
            lineage=lineage,
        )

    # ------------------------------------------------------------------ read / verify
    def get(self, who: Principal, run_id: int) -> RunView:
        stored = self._store.get_run(who, run_id)
        return RunView(
            run=stored.record, result=StressResult.model_validate_json(stored.result_json)
        )

    def snapshot_of(self, who: Principal, run_id: int) -> dict[str, object]:
        stored = self._store.get_run(who, run_id)
        loaded: dict[str, object] = json.loads(stored.inputs_json)
        return loaded

    def verify(self, who: Principal, run_id: int) -> Verification:
        stored: StoredRun = self._store.get_run(who, run_id)
        rec = stored.record
        try:
            scenario_obj = json.loads(stored.scenario_json)
            inputs_obj = json.loads(stored.inputs_json)
            scenario = ScenarioInput.model_validate(scenario_obj)
            snapshot = Snapshot.model_validate(inputs_obj["snapshot"])
            buffers = {k: Decimal(v) for k, v in inputs_obj["buffers"].items()}
            recomputed = canonical(engine.run(scenario, snapshot, buffers).model_dump(mode="json"))
            input_ok = (
                sha256(canonical({"scenario": scenario_obj, "inputs": inputs_obj}))
                == rec.input_hash
            )
        except (ValueError, KeyError, TypeError, ArithmeticError):
            return Verification(
                run_id=run_id,
                input_hash_ok=False,
                result_reproduced=False,
                result_hash_ok=False,
                signature_ok=False,
                scenario_matches_version=False,
                ok=False,
            )
        reproduced = recomputed == stored.result_json
        hash_ok = sha256(stored.result_json) == rec.result_hash
        sig_ok = self._signer.verify(_message(who.tenant_id, rec), rec.signature)
        version = self._store.get_scenario(who, rec.scenario_key, rec.scenario_version)
        same = canonical(version.scenario.model_dump(mode="json")) == stored.scenario_json
        return Verification(
            run_id=run_id,
            input_hash_ok=input_ok,
            result_reproduced=reproduced,
            result_hash_ok=hash_ok,
            signature_ok=sig_ok,
            scenario_matches_version=same,
            ok=all((input_ok, reproduced, hash_ok, sig_ok, same)),
        )
