from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailAuthBackend(ModelBackend):
    """Authenticate with username or email (case-insensitive for email)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        login = username or kwargs.get(User.USERNAME_FIELD)
        if not login or password is None:
            return None

        if "@" in login:
            lookup = {"email__iexact": login}
        else:
            lookup = {User.USERNAME_FIELD: login}

        try:
            user = User.objects.get(**lookup)
        except User.DoesNotExist:
            User().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
