from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, View

from audit.models import AuditLog
from audit.services import write_audit_log
from .forms import UserCreateForm, UserEditForm, UserPasswordForm
from .models import User


def is_protected_user(user):
    return user.is_superuser


def _user_to_audit_dict(user):
    return {
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "groups": sorted(user.groups.values_list("name", flat=True)),
    }


def _log_user_change(request, action, user, old=None, new=None):
    write_audit_log(
        entity_type=AuditLog.ENTITY_USER,
        entity_id=user.pk,
        action=action,
        request=request,
        source=AuditLog.SOURCE_UI,
        old_values=old,
        new_values=new,
    )


def _warn_if_no_groups(request, user):
    if not user.groups.exists():
        messages.warning(
            request,
            f"Пользователь «{user.username}» не состоит ни в одной группе и не имеет доступа к данным.",
        )


class UserListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "accounts.view_user"
    model = User
    template_name = "users/list.html"
    context_object_name = "users"
    ordering = ["username"]

    def get_queryset(self):
        return User.objects.prefetch_related("groups").order_by("username")


class UserCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "accounts.add_user"
    model = User
    form_class = UserCreateForm
    template_name = "users/form.html"
    success_url = reverse_lazy("accounts:list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = "Новый пользователь"
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        _log_user_change(
            self.request,
            AuditLog.ACTION_CREATE,
            self.object,
            new=_user_to_audit_dict(self.object),
        )
        messages.success(self.request, f"Пользователь «{self.object.username}» создан.")
        _warn_if_no_groups(self.request, self.object)
        return response


class UserUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "accounts.change_user"
    model = User
    form_class = UserEditForm
    template_name = "users/form.html"
    success_url = reverse_lazy("accounts:list")
    context_object_name = "edited_user"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if is_protected_user(obj):
            raise PermissionDenied
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Редактировать пользователя «{self.object.username}»"
        ctx["edited_user"] = self.object
        return ctx

    def form_valid(self, form):
        old_values = _user_to_audit_dict(User.objects.get(pk=self.object.pk))
        response = super().form_valid(form)
        _log_user_change(
            self.request,
            AuditLog.ACTION_UPDATE,
            self.object,
            old=old_values,
            new=_user_to_audit_dict(self.object),
        )
        messages.success(self.request, f"Пользователь «{self.object.username}» обновлён.")
        _warn_if_no_groups(self.request, self.object)
        return response


class UserPasswordView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "accounts.change_user"
    template_name = "users/password.html"

    def get_user(self, pk):
        return get_object_or_404(User, pk=pk)

    def get(self, request, pk):
        user = self.get_user(pk)
        if is_protected_user(user):
            raise PermissionDenied
        form = UserPasswordForm(target_user=user)
        return render(request, self.template_name, {"form": form, "target_user": user})

    def post(self, request, pk):
        user = self.get_user(pk)
        if is_protected_user(user):
            raise PermissionDenied
        form = UserPasswordForm(request.POST, target_user=user)
        if form.is_valid():
            user.set_password(form.cleaned_data["password1"])
            user.save(update_fields=["password"])
            _log_user_change(
                request,
                AuditLog.ACTION_UPDATE,
                user,
                new={"username": user.username, "password_changed": True},
            )
            messages.success(request, f"Пароль пользователя «{user.username}» изменён.")
            return redirect("accounts:list")
        return render(request, self.template_name, {"form": form, "target_user": user})


class UserSetActiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "accounts.change_user"
    target_active = True

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if is_protected_user(user):
            raise PermissionDenied
        if not self.target_active and user == request.user:
            messages.error(request, "Нельзя деактивировать самого себя.")
            return redirect("accounts:list")
        old_values = _user_to_audit_dict(user)
        user.is_active = self.target_active
        user.save(update_fields=["is_active"])
        _log_user_change(
            request,
            AuditLog.ACTION_UPDATE,
            user,
            old=old_values,
            new=_user_to_audit_dict(user),
        )
        verb = "активирован" if self.target_active else "деактивирован"
        messages.success(request, f"Пользователь «{user.username}» {verb}.")
        return redirect("accounts:list")


class UserDeactivateView(UserSetActiveView):
    target_active = False


class UserActivateView(UserSetActiveView):
    target_active = True
