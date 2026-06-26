from flask import g


LOCAL_USER_ID = "local"


def current_user_id() -> str:
    user = getattr(g, "current_user", None)
    if user and user.get("id"):
        return user["id"]
    return LOCAL_USER_ID
