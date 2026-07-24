from contextvars import ContextVar, Token

LOCAL_ACTOR = "local-reviewer"
_actor: ContextVar[str] = ContextVar("tuneforge_actor", default=LOCAL_ACTOR)


def current_actor() -> str:
    return _actor.get()


def set_actor(value: str) -> Token[str]:
    return _actor.set(value)


def reset_actor(token: Token[str]) -> None:
    _actor.reset(token)
