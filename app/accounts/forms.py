from django import forms
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.db.models import Case, IntegerField, When

from .models import User
from .permissions import GROUP_DESCRIPTIONS


def ordered_groups_queryset():
    order_cases = [
        When(name=group_name, then=index)
        for index, group_name in enumerate(GROUP_DESCRIPTIONS)
    ]
    return (
        Group.objects.annotate(
            role_order=Case(
                *order_cases,
                default=len(GROUP_DESCRIPTIONS),
                output_field=IntegerField(),
            )
        )
        .order_by("role_order", "name")
    )


class PasswordPairMixin(forms.Form):
    password1 = forms.CharField(label="Пароль", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Подтверждение пароля", widget=forms.PasswordInput)

    def get_password_validation_user(self):
        """Пользователь для UserAttributeSimilarityValidator. По умолчанию недоступен."""
        return None

    def clean(self):
        cleaned = super().clean()
        pw1 = cleaned.get("password1")
        pw2 = cleaned.get("password2")
        if pw1 and pw2 and pw1 != pw2:
            self.add_error("password2", "Пароли не совпадают.")
        if pw1:
            try:
                validate_password(pw1, user=self.get_password_validation_user())
            except forms.ValidationError as e:
                self.add_error("password1", e)
        return cleaned


_USER_META_FIELDS = ("username", "first_name", "last_name", "email", "is_active")


class RoleHelpMixin(forms.Form):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Группы",
    )

    def configure_groups_field(self):
        self.fields["groups"].queryset = ordered_groups_queryset()

    @property
    def group_options(self):
        field = self["groups"]
        selected_values = {
            str(getattr(value, "pk", value))
            for value in field.value() or []
        }
        options = []
        for index, group in enumerate(self.fields["groups"].queryset):
            options.append({
                "group": group,
                "description": GROUP_DESCRIPTIONS.get(group.name, ""),
                "checked": str(group.pk) in selected_values,
                "id": f"{field.auto_id}_{index}",
            })
        return options


class UserCreateForm(PasswordPairMixin, RoleHelpMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = _USER_META_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_groups_field()

    def get_password_validation_user(self):
        # self.instance ещё не заполнен (ModelForm делает это в _post_clean,
        # после clean()), поэтому собираем несохранённого User из cleaned_data.
        cleaned = self.cleaned_data
        return User(
            username=cleaned.get("username", ""),
            first_name=cleaned.get("first_name", ""),
            last_name=cleaned.get("last_name", ""),
            email=cleaned.get("email", ""),
        )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            user.groups.set(self.cleaned_data.get("groups", []))
        return user


class UserEditForm(RoleHelpMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = _USER_META_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_groups_field()
        if self.instance.pk:
            self.initial["groups"] = self.instance.groups.all()

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.groups.set(self.cleaned_data.get("groups", []))
        return user


class UserPasswordForm(PasswordPairMixin, forms.Form):
    def __init__(self, *args, target_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_user = target_user
        self.fields["password1"].label = "Новый пароль"

    def get_password_validation_user(self):
        return self.target_user

    def clean(self):
        cleaned = super().clean()
        if self.target_user is not None and self.target_user.is_superuser:
            raise forms.ValidationError("Нельзя сменить пароль защищённого пользователя.")
        return cleaned
