"""Administration, account state, passwords handled by an admin, notifications, waitlist."""

import pytest

from tests.conftest import PASSWORD, buy, make_admin, make_listing, make_user

pytestmark = pytest.mark.anyio


async def _login(client, email: str, password: str):
    return await client.post("/api/auth/login", json={"email": email, "password": password})


# ---------------------------------------------------------------- access


async def test_nobody_can_sign_up_as_admin(client) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"role": "admin", "full_name": "x y", "email": "x@test.iq", "password": PASSWORD},
    )
    assert r.status_code == 422


async def test_admin_routes_need_an_admin(client) -> None:
    investor = await make_user(client)
    assert (await client.get("/api/admin/overview")).status_code == 401
    r = await client.get("/api/admin/overview", headers=investor.headers)
    assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN"


async def test_overview_counts(client) -> None:
    admin = await make_admin(client)
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100", 21)
    investor = await make_user(client, kyc=True)
    assert (await buy(client, investor, listing["id"], "2")).status_code == 201
    body = (await client.get("/api/admin/overview", headers=admin.headers)).json()
    assert body["sellers"] == 1 and body["investors"] == 1
    assert body["transactions"] == 1 and body["active_listings"] == 1
    assert body["interest"] == {"real_estate": 0, "oil": 0}


# ---------------------------------------------------------------- accounts


async def test_deactivation_blocks_login_tokens_and_listings(client) -> None:
    admin = await make_admin(client)
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "50", 21)

    r = await client.patch(
        f"/api/admin/users/{seller.id}", json={"is_active": False}, headers=admin.headers
    )
    assert r.status_code == 200 and r.json()["is_active"] is False
    # The open session stops working, and so does signing in
    assert (await client.get("/api/users/me", headers=seller.headers)).status_code == 401
    r = await _login(client, seller.user["email"], PASSWORD)
    assert r.status_code == 403 and r.json()["error_code"] == "ACCOUNT_DISABLED"
    # Its listing left the market
    assert (await client.get(f"/api/listings/{listing['id']}")).json()["status"] == "suspended"

    await client.patch(
        f"/api/admin/users/{seller.id}", json={"is_active": True}, headers=admin.headers
    )
    assert (await _login(client, seller.user["email"], PASSWORD)).status_code == 200


async def test_admin_cannot_touch_admins(client) -> None:
    admin = await make_admin(client)
    other = await make_admin(client)
    r = await client.patch(
        f"/api/admin/users/{other.id}", json={"is_active": False}, headers=admin.headers
    )
    assert r.status_code == 403


async def test_forgot_password_then_temporary_password(client) -> None:
    admin = await make_admin(client)
    investor = await make_user(client)
    email = investor.user["email"]

    r = await client.post("/api/auth/forgot-password", json={"email": email})
    assert r.status_code == 202
    # The same reply for an unknown e-mail: nothing reveals who is registered
    unknown = await client.post("/api/auth/forgot-password", json={"email": "nobody@test.iq"})
    assert unknown.json()["message"] == r.json()["message"]

    pending = (await client.get("/api/admin/password-requests", headers=admin.headers)).json()
    request = next(p for p in pending if p["email"] == email)
    assert request["user_name"] == investor.user["full_name"]
    bell = (await client.get("/api/notifications", headers=admin.headers)).json()
    assert bell["items"][0]["kind"] == "password_reset_request"

    r = await client.post(f"/api/admin/users/{investor.id}/reset-password", headers=admin.headers)
    assert r.status_code == 200
    temporary = r.json()["temporary_password"]
    assert len(temporary) == 12 and r.json()["user"]["must_change_password"] is True

    # The old session ended; the temporary password signs in and asks for a new one
    assert (await client.get("/api/users/me", headers=investor.headers)).status_code == 401
    assert (await _login(client, email, PASSWORD)).status_code == 401
    login = await _login(client, email, temporary)
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = await client.post(
        "/api/users/me/password",
        json={"current_password": temporary, "new_password": "NewPass@2026"},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is False
    assert (await _login(client, email, "NewPass@2026")).status_code == 200

    resolved = (
        await client.get(
            "/api/admin/password-requests", params={"status": "resolved"}, headers=admin.headers
        )
    ).json()
    assert any(p["email"] == email for p in resolved)


async def test_change_password_needs_the_current_one(client) -> None:
    investor = await make_user(client)
    r = await client.post(
        "/api/users/me/password",
        json={"current_password": "wrong-one", "new_password": "NewPass@2026"},
        headers=investor.headers,
    )
    assert r.status_code == 401 and r.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_dismiss_password_request(client) -> None:
    admin = await make_admin(client)
    investor = await make_user(client)
    await client.post("/api/auth/forgot-password", json={"email": investor.user["email"]})
    request = (await client.get("/api/admin/password-requests", headers=admin.headers)).json()[0]
    r = await client.patch(
        f"/api/admin/password-requests/{request['id']}",
        json={"status": "dismissed"},
        headers=admin.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "dismissed"


# ---------------------------------------------------------------- moderation and logs


async def test_listing_moderation_notifies_the_seller(client) -> None:
    admin = await make_admin(client)
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "50", 21)
    r = await client.patch(
        f"/api/admin/listings/{listing['id']}", json={"status": "suspended"}, headers=admin.headers
    )
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    bell = (await client.get("/api/notifications", headers=seller.headers)).json()
    assert bell["items"][0]["kind"] == "listing_suspended"

    all_listings = (await client.get("/api/admin/listings", headers=admin.headers)).json()
    assert all_listings["total"] == 1

    audit = (
        await client.get(
            "/api/admin/audit",
            params={"event_type": "admin_listing_moderated"},
            headers=admin.headers,
        )
    ).json()
    assert audit["total"] == 1 and audit["items"][0]["actor_id"] == admin.id


async def test_user_search(client) -> None:
    admin = await make_admin(client)
    await make_user(client, name="حيدر الكاظمي")
    await make_user(client, "seller")
    r = await client.get("/api/admin/users", params={"q": "حيدر"}, headers=admin.headers)
    assert r.json()["total"] == 1
    r = await client.get("/api/admin/users", params={"role": "seller"}, headers=admin.headers)
    assert r.json()["total"] == 1


# ---------------------------------------------------------------- notifications


async def test_notifications_mark_read(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "50", 21)
    investor = await make_user(client, kyc=True)
    await buy(client, investor, listing["id"], "1")
    await buy(client, investor, listing["id"], "1")

    bell = (await client.get("/api/notifications", headers=investor.headers)).json()
    assert bell["unread_count"] == 2
    first = bell["items"][0]["id"]
    after = (
        await client.post(
            "/api/notifications/read", json={"ids": [first]}, headers=investor.headers
        )
    ).json()
    assert after["unread_count"] == 1
    after = (await client.post("/api/notifications/read", json={}, headers=investor.headers)).json()
    assert after["unread_count"] == 0 and all(n["read"] for n in after["items"])


# ---------------------------------------------------------------- waitlist


async def test_interest_is_public_and_idempotent(client) -> None:
    admin = await make_admin(client)
    body = {"email": "Someone@Test.iq", "asset_class": "real_estate"}
    first = await client.post("/api/interest", json=body)
    again = await client.post("/api/interest", json=body)
    assert first.status_code == again.status_code == 202
    await client.post("/api/interest", json={"email": "someone@test.iq", "asset_class": "oil"})
    signups = (await client.get("/api/admin/interest", headers=admin.headers)).json()
    assert sorted(s["asset_class"] for s in signups) == ["oil", "real_estate"]
    assert all(s["email"] == "someone@test.iq" for s in signups)
    r = await client.post("/api/interest", json={"email": "a@test.iq", "asset_class": "cars"})
    assert r.status_code == 422
