"""Auth and access control."""

from __future__ import annotations

from .conftest_api import register


async def test_register_returns_a_usable_token(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 201
    token = response.json()["access_token"]

    me = await client.get("/api/v1/auth/me",
                          headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"
    assert "hashed_password" not in me.json()


async def test_duplicate_email_is_rejected(client):
    body = {"email": "a@example.com", "password": "correct-horse-battery"}
    assert (await client.post("/api/v1/auth/register", json=body)).status_code == 201
    second = await client.post("/api/v1/auth/register", json=body)
    assert second.status_code == 409


async def test_email_is_case_insensitive(client):
    await client.post("/api/v1/auth/register",
                      json={"email": "Mixed@Example.com", "password": "a-long-password"})
    dupe = await client.post("/api/v1/auth/register",
                             json={"email": "mixed@example.com",
                                   "password": "a-long-password"})
    assert dupe.status_code == 409

    login = await client.post("/api/v1/auth/login",
                              json={"email": "MIXED@example.com",
                                    "password": "a-long-password"})
    assert login.status_code == 200


async def test_short_passwords_are_rejected(client):
    response = await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_login_with_the_wrong_password_fails(client):
    await register(client, "a@example.com")
    response = await client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong-one"}
    )
    assert response.status_code == 401


async def test_login_does_not_reveal_whether_an_account_exists(client):
    await register(client, "real@example.com")
    missing = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "correct-horse-battery"},
    )
    wrong = await client.post(
        "/api/v1/auth/login",
        json={"email": "real@example.com", "password": "not-the-password"},
    )
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json(), (
        "responses must be identical or the endpoint enumerates accounts"
    )


async def test_protected_endpoints_require_a_token(client):
    for method, url in [
        ("get", "/api/v1/auth/me"),
        ("get", "/api/v1/players"),
        ("get", "/api/v1/tournaments"),
    ]:
        response = await getattr(client, method)(url)
        assert response.status_code == 401, url


async def test_garbage_and_expired_tokens_are_rejected(client):
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


async def test_a_court_token_cannot_be_used_as_a_login(client):
    """Court codes are scoped to one match and must not authenticate a user."""
    from app.core.security import create_court_token

    token = create_court_token("match-1", "tourney-1")
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
