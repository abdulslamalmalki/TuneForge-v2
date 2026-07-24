from app.domain import auth


def test_password_hash_is_salted_and_verifiable():
    first = auth.hash_password("correct horse battery staple")
    second = auth.hash_password("correct horse battery staple")

    assert first != second
    assert "correct horse" not in first
    assert auth.verify_password("correct horse battery staple", first)
    assert not auth.verify_password("incorrect password", first)


def test_first_administrator_is_one_time_and_security_event_is_append_only(db_session):
    user = auth.create_first_administrator(
        db_session,
        username="owner",
        display_name="Project Owner",
        password="a sufficiently long password",
    )

    assert user.role == auth.ROLE_ADMINISTRATOR
    assert user.status == auth.STATUS_ACTIVE
    assert [event.action for event in auth.list_events(db_session)] == ["user_created"]

    try:
        auth.create_first_administrator(
            db_session,
            username="other",
            display_name="Other Owner",
            password="another sufficiently long password",
        )
    except auth.AuthError as error:
        assert "already" in error.message
    else:
        raise AssertionError("A second first-run administrator was accepted")


def test_disabling_user_revokes_sessions_and_preserves_account(db_session):
    owner = auth.create_first_administrator(
        db_session,
        username="owner",
        display_name="Project Owner",
        password="a sufficiently long password",
    )
    reviewer = auth.create_user(
        db_session,
        username="reviewer",
        display_name="Evidence Reviewer",
        password="another sufficiently long password",
        role=auth.ROLE_REVIEWER,
        actor=owner.username,
    )
    token = auth.create_session(db_session, reviewer)
    principal = auth.Principal(owner.id, owner.username, owner.display_name, owner.role)

    auth.disable_user(db_session, reviewer.id, actor=principal)

    assert auth.principal_for_token(db_session, token) is None
    assert auth.list_users(db_session)[1].status == auth.STATUS_DISABLED
    assert [event.action for event in reversed(auth.list_events(db_session))] == [
        "user_created",
        "user_created",
        "login_succeeded",
        "user_disabled",
    ]
