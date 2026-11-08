"""Integration tests for PPM Sources and Location Registrations (P2a-WP1).

Covers:
- C32: Monorepo registration (three scopes on one monorepo source: proj-app-a, proj-app-b, proj-shared-gateway).
- Duplicate (source, metadata_root) in same tenant -> 409.
- Same physical folder under a different source_id -> 409.
- Same repo_identity under a different source_id -> 409.
- Multiple scopes for a single project (W7-3) -> succeeds.
- C42: Copied repo with same project_id and divergent repo_identity -> quarantined (state: suspended) + identity_conflict record; never merged.
- Approval workflow:
  - Registrant cannot approve own location -> 403.
  - Non-owner/non-admin principal cannot approve -> 403.
  - Cross-tenant approval -> 404.
  - Cross-tenant GET -> 404.
  - Successful approval by authorized admin/owner -> active state.
- Location negatives from examples/ and tools/validate_package.py rejected:
  - follow_symlinks: True -> 422.
  - alias escapes root (..) -> 422.
  - absolute alias -> 422.
  - location active without approval on POST -> 422.
  - non-absolute root_path -> 422.
  - duplicate alias in category -> 422.
- Forbidden client-declared tenant/principal injection -> 422.
- Outbox events written on state transitions.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.api.app import app
from eap.api.dependencies import Principal, get_db, get_principal
from eap.db import identity_conflicts, outbox, project_scopes

ROOT = Path(__file__).resolve().parents[1]
LOCATION_EXAMPLE_PATH = ROOT / "examples" / "location-registration.json"
MONOREPO_EXAMPLE_PATH = ROOT / "examples" / "locations-monorepo.json"


@pytest.fixture
def api_client(db_engine: Engine):
    """Test async client connected to real PostgreSQL database with dependency injection."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db

    current_principal = Principal(
        tenant_id="tenant-pilot-alpha",
        principal_id="principal-owner-alpha",
        roles=["owner", "admin"],
    )

    def _get_current_principal() -> Principal:
        return current_principal

    app.dependency_overrides[get_principal] = _get_current_principal
    transport = ASGITransport(app=app)

    class ClientManager:
        def __init__(self):
            self.http = AsyncClient(transport=transport, base_url="http://testserver")

        def set_principal(self, tenant_id: str, principal_id: str, roles: list[str]) -> None:
            nonlocal current_principal
            current_principal = Principal(tenant_id=tenant_id, principal_id=principal_id, roles=roles)

    manager = ClientManager()
    yield manager
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_c32_monorepo_three_scopes_on_one_source_succeed(api_client, db_conn: sa.Connection) -> None:
    """C32 registration portion: proj-app-a, proj-app-b, proj-shared-gateway sharing src-example-monorepo succeed."""
    client = api_client.http
    tenant = "tenant-c32-monorepo"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-reg-user", roles=["admin"])

    # 1. Register physical source
    res_source = await client.post(
        "/v1/ppm/sources",
        json={
            "source_id": "src-example-monorepo",
            "canonical_root": "D:/projects/example-monorepo",
            "repo_identity": "git:example-monorepo@root-commit-0001",
            "host_os": "windows",
        },
    )
    assert res_source.status_code == 201, res_source.text
    source_data = res_source.json()
    assert source_data["source_id"] == "src-example-monorepo"
    assert source_data["canonical_root"] == "d:/projects/example-monorepo"

    # 2. Register Scope 1: Project App A
    mono_items = json.loads(MONOREPO_EXAMPLE_PATH.read_text(encoding="utf-8"))
    loc_a = copy.deepcopy(mono_items[0])
    loc_a["state"] = "proposed"
    loc_a["approved_by_ref"] = None

    res_a = await client.post("/v1/ppm/locations", json=loc_a)
    assert res_a.status_code == 201, res_a.text
    data_a = res_a.json()
    assert data_a["location_id"] == "loc-mono-a"
    assert data_a["project_id"] == "proj-app-a"
    assert data_a["state"] == "proposed"

    # 3. Register Scope 2: Project App B
    loc_b = copy.deepcopy(mono_items[1])
    loc_b["state"] = "proposed"
    loc_b["approved_by_ref"] = None

    res_b = await client.post("/v1/ppm/locations", json=loc_b)
    assert res_b.status_code == 201, res_b.text
    data_b = res_b.json()
    assert data_b["location_id"] == "loc-mono-b"
    assert data_b["project_id"] == "proj-app-b"
    assert data_b["state"] == "proposed"

    # 4. Register Scope 3: Project Shared Gateway
    loc_shared = copy.deepcopy(mono_items[2])
    loc_shared["state"] = "proposed"
    loc_shared["approved_by_ref"] = None

    res_shared = await client.post("/v1/ppm/locations", json=loc_shared)
    assert res_shared.status_code == 201, res_shared.text
    data_shared = res_shared.json()
    assert data_shared["location_id"] == "loc-mono-shared"
    assert data_shared["project_id"] == "proj-shared-gateway"
    assert data_shared["state"] == "proposed"

    # Verify database state
    scopes_count = db_conn.execute(
        sa.select(sa.func.count())
        .select_from(project_scopes)
        .where(
            project_scopes.c.tenant_id == tenant,
            project_scopes.c.source_id == "src-example-monorepo",
        )
    ).scalar()
    assert scopes_count == 3


@pytest.mark.anyio
async def test_duplicate_source_metadata_root_rejected_409(api_client) -> None:
    """Duplicate (source, metadata_root) under same project -> 409 DUPLICATE_LOCATION."""
    client = api_client.http
    tenant = "tenant-dup-loc"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-1", roles=["admin"])

    loc = json.loads(LOCATION_EXAMPLE_PATH.read_text(encoding="utf-8"))
    loc["source_id"] = f"src-{tenant}"
    loc["location_id"] = "loc-first"
    loc["metadata_root"] = "services/api"
    loc["state"] = "proposed"
    loc["approved_by_ref"] = None

    res1 = await client.post("/v1/ppm/locations", json=loc)
    assert res1.status_code == 201

    # Attempt second registration with same source_id and metadata_root
    loc2 = copy.deepcopy(loc)
    loc2["location_id"] = "loc-second"
    res2 = await client.post("/v1/ppm/locations", json=loc2)
    assert res2.status_code == 409
    assert "duplicate location" in res2.text.lower()


@pytest.mark.anyio
async def test_metadata_root_claimed_by_two_projects_rejected_409(api_client) -> None:
    """Metadata root on a source claimed by a second project -> 409 METADATA_ROOT_CONFLICT."""
    client = api_client.http
    tenant = "tenant-conflict-meta"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-1", roles=["admin"])

    loc = json.loads(LOCATION_EXAMPLE_PATH.read_text(encoding="utf-8"))
    loc["source_id"] = f"src-{tenant}"
    loc["location_id"] = "loc-orig"
    loc["project_id"] = "proj-alpha"
    loc["metadata_root"] = "apps/shared-root"
    loc["state"] = "proposed"
    loc["approved_by_ref"] = None

    res1 = await client.post("/v1/ppm/locations", json=loc)
    assert res1.status_code == 201

    # Second project claims same metadata_root on same source
    loc2 = copy.deepcopy(loc)
    loc2["location_id"] = "loc-other-proj"
    loc2["project_id"] = "proj-beta"  # DIFFERENT PROJECT

    res2 = await client.post("/v1/ppm/locations", json=loc2)
    assert res2.status_code == 409
    assert "already claimed by project 'proj-alpha'" in res2.text


@pytest.mark.anyio
async def test_same_folder_under_different_source_rejected_409(api_client) -> None:
    """Same physical canonical root registered under a second source_id -> 409."""
    client = api_client.http
    tenant = "tenant-folder-dup"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-1", roles=["admin"])

    # Register first source
    res1 = await client.post(
        "/v1/ppm/sources",
        json={
            "source_id": "src-primary",
            "canonical_root": "C:\\Repos\\ProjectAlpha",
            "repo_identity": "git@github.com:example/alpha.git",
            "host_os": "windows",
        },
    )
    assert res1.status_code == 201

    # Attempt to register same folder under differing source_id (using case/slash variant)
    res2 = await client.post(
        "/v1/ppm/sources",
        json={
            "source_id": "src-secondary",
            "canonical_root": "c:/repos/projectalpha",  # Same folder after case normalization
            "repo_identity": "git@github.com:example/alpha-clone.git",
            "host_os": "windows",
        },
    )
    assert res2.status_code == 409
    assert "already registered under source_id 'src-primary'" in res2.text


@pytest.mark.anyio
async def test_same_repo_identity_under_different_source_rejected_409(api_client) -> None:
    """Same repo_identity registered under a second source_id -> 409."""
    client = api_client.http
    tenant = "tenant-repo-dup"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-1", roles=["admin"])

    res1 = await client.post(
        "/v1/ppm/sources",
        json={
            "source_id": "src-git-1",
            "canonical_root": "/repos/checkout-1",
            "repo_identity": "git:shared-repo-12345",
            "host_os": "linux",
        },
    )
    assert res1.status_code == 201

    res2 = await client.post(
        "/v1/ppm/sources",
        json={
            "source_id": "src-git-2",
            "canonical_root": "/repos/checkout-2",  # Different directory
            "repo_identity": "git:shared-repo-12345",  # SAME repo_identity
            "host_os": "linux",
        },
    )
    assert res2.status_code == 409
    assert "already registered under source_id 'src-git-1'" in res2.text


@pytest.mark.anyio
async def test_w7_3_single_project_multiple_scopes_allowed(api_client, db_conn: sa.Connection) -> None:
    """W7-3: One project registering multiple scopes across directories succeeds."""
    client = api_client.http
    tenant = "tenant-w7-3"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-1", roles=["admin"])

    loc1 = json.loads(LOCATION_EXAMPLE_PATH.read_text(encoding="utf-8"))
    loc1["source_id"] = f"src-{tenant}"
    loc1["location_id"] = "loc-scope-1"
    loc1["project_id"] = "proj-multi-scope"
    loc1["metadata_root"] = "component-1"
    loc1["state"] = "proposed"
    loc1["approved_by_ref"] = None

    res1 = await client.post("/v1/ppm/locations", json=loc1)
    assert res1.status_code == 201

    loc2 = copy.deepcopy(loc1)
    loc2["location_id"] = "loc-scope-2"
    loc2["project_id"] = "proj-multi-scope"  # SAME project
    loc2["metadata_root"] = "component-2"  # DIFFERENT metadata_root

    res2 = await client.post("/v1/ppm/locations", json=loc2)
    assert res2.status_code == 201

    count = db_conn.execute(
        sa.select(sa.func.count())
        .select_from(project_scopes)
        .where(
            project_scopes.c.tenant_id == tenant,
            project_scopes.c.project_id == "proj-multi-scope",
        )
    ).scalar()
    assert count == 2


@pytest.mark.anyio
async def test_c42_copied_repo_with_same_project_id_quarantined(api_client, db_conn: sa.Connection) -> None:
    """C42: A second location claiming an existing project_id with differing repo_identity.

    Expected:
    - Registered as quarantined (state='suspended' per contract enum).
    - An identity_conflict record is created in the database.
    - An outbox event is emitted.
    - Locations are never merged.
    """
    client = api_client.http
    tenant = "tenant-c42-conflict"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-registrant", roles=["admin"])

    # 1. Register Source 1 and Location 1 for Project X
    await client.post(
        "/v1/ppm/sources",
        json={
            "source_id": "src-repo-orig",
            "canonical_root": "/repos/original",
            "repo_identity": "git:repo-original@commit-1",
            "host_os": "linux",
        },
    )

    loc1 = json.loads(LOCATION_EXAMPLE_PATH.read_text(encoding="utf-8"))
    loc1["location_id"] = f"loc-{tenant}-orig"
    loc1["project_id"] = "proj-replicated"
    loc1["source_id"] = "src-repo-orig"
    loc1["root_path"] = "/repos/original"
    loc1["identity"] = {
        "canonical_root_fingerprint": "fp-1",
        "repo_identity": "git:repo-original@commit-1",
    }
    loc1["state"] = "proposed"
    loc1["approved_by_ref"] = None

    res1 = await client.post("/v1/ppm/locations", json=loc1)
    assert res1.status_code == 201
    assert res1.json()["state"] == "proposed"

    # 2. Register Source 2 (copied repo)
    await client.post(
        "/v1/ppm/sources",
        json={
            "source_id": "src-repo-copied",
            "canonical_root": "/repos/copied",
            "repo_identity": "git:repo-copied@commit-99",  # DIFFERENT repo_identity
            "host_os": "linux",
        },
    )

    # 3. Register Location 2 claiming the SAME project_id on Source 2
    loc2 = copy.deepcopy(loc1)
    loc2["location_id"] = f"loc-{tenant}-copy"
    loc2["project_id"] = "proj-replicated"  # SAME project_id
    loc2["source_id"] = "src-repo-copied"
    loc2["root_path"] = "/repos/copied"
    loc2["identity"] = {
        "canonical_root_fingerprint": "fp-2",
        "repo_identity": "git:repo-copied@commit-99",
    }

    res2 = await client.post("/v1/ppm/locations", json=loc2)
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["location_id"] == f"loc-{tenant}-copy"
    assert data2["state"] == "suspended"  # Quarantined per contract enum

    # 4. Verify identity_conflict record exists in DB
    conflict_row = db_conn.execute(
        sa.select(identity_conflicts).where(
            identity_conflicts.c.tenant_id == tenant,
            identity_conflicts.c.location_id == f"loc-{tenant}-copy",
        )
    ).mappings().first()
    assert conflict_row is not None
    assert conflict_row["project_id"] == "proj-replicated"
    assert conflict_row["conflicting_source_id"] == "src-repo-orig"
    assert conflict_row["conflicting_repo_identity"] == "git:repo-original@commit-1"

    # 5. Verify outbox event written
    outbox_event = db_conn.execute(
        sa.select(outbox).where(
            outbox.c.tenant_id == tenant,
            outbox.c.event_type == "ppm.identity_conflict.detected",
        )
    ).mappings().first()
    assert outbox_event is not None
    assert outbox_event["payload"]["payload"]["claimed_project_id"] == "proj-replicated"

    # 6. Verify approving the quarantined location is rejected
    api_client.set_principal(tenant_id=tenant, principal_id="principal-approver", roles=["owner", "admin"])
    res_approve = await client.post(f"/v1/ppm/locations/loc-{tenant}-copy/approve")
    assert res_approve.status_code == 409
    assert "quarantined" in res_approve.text.lower()


@pytest.mark.anyio
async def test_approval_authority_and_isolation(api_client, db_conn: sa.Connection) -> None:
    """Approval authority rules:

    - Registrant cannot approve own location -> 403.
    - Non-owner/non-admin principal cannot approve -> 403.
    - Cross-tenant principal -> 404.
    - Authorized approver -> 200 (state: active).
    """
    client = api_client.http
    tenant_a = "tenant-approval-alpha"
    tenant_b = "tenant-approval-beta"

    # 1. Registrant registers location in Tenant A
    api_client.set_principal(tenant_id=tenant_a, principal_id="user-registrant", roles=["admin"])

    loc = json.loads(LOCATION_EXAMPLE_PATH.read_text(encoding="utf-8"))
    loc["source_id"] = f"src-{tenant_a}"
    loc["location_id"] = "loc-approve-test"
    loc["state"] = "proposed"
    loc["approved_by_ref"] = None

    res_reg = await client.post("/v1/ppm/locations", json=loc)
    assert res_reg.status_code == 201

    # 2. Registrant attempts to approve own registration -> 403 Forbidden
    res_self = await client.post("/v1/ppm/locations/loc-approve-test/approve")
    assert res_self.status_code == 403
    assert "cannot approve own location" in res_self.text.lower()

    # 3. Non-owner / non-admin user in same tenant attempts approval -> 403 Forbidden
    api_client.set_principal(tenant_id=tenant_a, principal_id="user-viewer", roles=["viewer", "developer"])
    res_non_owner = await client.post("/v1/ppm/locations/loc-approve-test/approve")
    assert res_non_owner.status_code == 403
    assert "owner/admin role" in res_non_owner.text.lower()

    # 4. Cross-tenant principal (even if owner of tenant B) -> 404 Not Found
    api_client.set_principal(tenant_id=tenant_b, principal_id="owner-beta", roles=["owner", "admin"])
    res_cross = await client.post("/v1/ppm/locations/loc-approve-test/approve")
    assert res_cross.status_code == 404
    assert "not found" in res_cross.text.lower()

    # Cross-tenant GET also 404
    res_cross_get = await client.get("/v1/ppm/locations/loc-approve-test")
    assert res_cross_get.status_code == 404

    # 5. Different owner principal in Tenant A approves -> 200 OK
    api_client.set_principal(tenant_id=tenant_a, principal_id="user-owner", roles=["owner", "admin"])
    res_approved = await client.post("/v1/ppm/locations/loc-approve-test/approve")
    assert res_approved.status_code == 200, res_approved.text
    data = res_approved.json()
    assert data["state"] == "active"
    assert data["approved_by_ref"] == "principal:user-owner"

    # Verify project_scopes status in DB is active
    ps_status = db_conn.execute(
        sa.select(project_scopes.c.status).where(
            project_scopes.c.tenant_id == tenant_a,
            project_scopes.c.id == "loc-approve-test",
        )
    ).scalar()
    assert ps_status == "active"

    # Verify outbox event ppm.location.approved written
    ev = db_conn.execute(
        sa.select(outbox).where(
            outbox.c.tenant_id == tenant_a,
            outbox.c.event_type == "ppm.location.approved",
        )
    ).mappings().first()
    assert ev is not None
    assert ev["payload"]["payload"]["location_id"] == "loc-approve-test"


@pytest.mark.anyio
async def test_location_negatives_from_validate_package_rejected_by_api(api_client) -> None:
    """All location negatives from examples/ and tools/validate_package.py rejected by the API."""
    client = api_client.http
    tenant = "tenant-negatives"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-reg", roles=["admin"])

    loc_base = json.loads(LOCATION_EXAMPLE_PATH.read_text(encoding="utf-8"))
    loc_base["source_id"] = f"src-{tenant}"

    # Case 1: follow_symlinks = True
    c1 = copy.deepcopy(loc_base)
    c1["follow_symlinks"] = True
    r1 = await client.post("/v1/ppm/locations", json=c1)
    assert r1.status_code == 422

    # Case 2: alias escapes root (..)
    c2 = copy.deepcopy(loc_base)
    c2["artifact_aliases"]["blueprint"] = ["../other/MASTER_BLUEPRINT.md"]
    r2 = await client.post("/v1/ppm/locations", json=c2)
    assert r2.status_code == 422

    # Case 3: absolute alias (C:/...)
    c3 = copy.deepcopy(loc_base)
    c3["artifact_aliases"]["architecture"] = ["C:/Users/x/arch.md"]
    r3 = await client.post("/v1/ppm/locations", json=c3)
    assert r3.status_code == 422

    # Case 4: location active without approval on POST
    c4 = copy.deepcopy(loc_base)
    c4["state"] = "active"
    c4["approved_by_ref"] = None
    r4 = await client.post("/v1/ppm/locations", json=c4)
    assert r4.status_code in (400, 422)

    # Case 5: root_path not absolute
    c5 = copy.deepcopy(loc_base)
    c5["root_path"] = "relative/path/to/repo"
    r5 = await client.post("/v1/ppm/locations", json=c5)
    assert r5.status_code == 422

    # Case 6: duplicate alias in category
    c6 = copy.deepcopy(loc_base)
    c6["artifact_aliases"]["architecture"] = ["docs/arch.md", "docs/arch.md"]
    r6 = await client.post("/v1/ppm/locations", json=c6)
    assert r6.status_code == 422


@pytest.mark.anyio
async def test_forbidden_identity_injection_rejected_422(api_client) -> None:
    """Client attempting to inject tenant_id or principal in headers, query, or body -> 422."""
    client = api_client.http

    # 1. Header injection
    r_hdr = await client.post(
        "/v1/ppm/sources",
        headers={"X-Tenant-Id": "forged-tenant"},
        json={"source_id": "s1", "canonical_root": "C:/a", "repo_identity": "r1"},
    )
    assert r_hdr.status_code == 422
    assert "forbidden" in r_hdr.text.lower()

    # 2. Query injection
    r_query = await client.get("/v1/ppm/sources?tenant_id=forged-tenant")
    assert r_query.status_code == 422
    assert "forbidden" in r_query.text.lower()

    # 3. Body injection
    r_body = await client.post(
        "/v1/ppm/sources",
        json={"tenant_id": "forged-tenant", "source_id": "s1", "canonical_root": "C:/a", "repo_identity": "r1"},
    )
    assert r_body.status_code == 422
    assert "forbidden" in r_body.text.lower()


@pytest.mark.anyio
async def test_sc04_canary_redaction_in_ppm_location_flows(
    api_client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SC-04 canary verification: canary never appears in logs or error responses for PPM APIs."""
    from eap.observability import register_secret

    canary = "EAP-CANARY-f81d4fae-7dec-11d0-a765-00a0c91e6bf6"
    register_secret(canary)

    client = api_client.http
    tenant = "tenant-sc04"
    api_client.set_principal(tenant_id=tenant, principal_id="principal-sc04", roles=["admin"])

    caplog.clear()

    # Trigger 422 with canary in field
    resp = await client.post(
        "/v1/ppm/sources",
        json={"tenant_id": f"forbidden-tenant-{canary}", "source_id": "s1", "canonical_root": "C:/a", "repo_identity": "r1"},
    )
    assert resp.status_code == 422

    # Verify canary never appears in response body
    assert canary not in resp.text

    # Verify canary never appears in captured log records
    for record in caplog.records:
        assert canary not in record.getMessage()
        if record.exc_text:
            assert canary not in record.exc_text

